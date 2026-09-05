"""使用本机 Mind 配置验证真实审批故障和后续 Turn 执行门。"""

import argparse
import asyncio
import contextlib
import datetime
import socket
from collections.abc import (
    AsyncIterator,
    Mapping,
)
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import StreamingResponse
from starlette.routing import Route

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.application.tools.coding_schemas import shell_command_input_schema
from agent.ports import (
    ApprovalSnapshotCallback,
    ModelEventStream,
    RecoveryStatusCallback,
    TransportRecoveryPhase,
)
from agent.protocol import (
    ModelEvent,
    ModelStreamRequest,
)
from agent.protocol.json_value import (
    JsonValue,
    freeze_json,
    thaw_object,
)
from infrastructure.config.preferences import Preferences
from infrastructure.config.runtime_paths import application_config_path
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.services.service_config import ServiceConfig
from protocol.client.tools import (
    ToolApprovalRequestError,
    post_tool_approval,
)
from protocol.schema.identifiers import (
    new_cid,
    new_request_id,
    new_sid,
    short_uid,
)
from protocol.schema.stream_events import ToolApprovalRequiredEvent
from protocol.schema.stream_events import TurnCompletedEvent
from protocol.schema.tool_approval import ToolApprovalSnapshot
from protocol.transport.endpoints import service_endpoints


_HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})


@dataclass(frozen=True, slots=True)
class LiveApprovalConfig:
    """保存线上审批验证使用的非敏感配置。"""

    domain: str
    pref_config: Mapping[str, JsonValue]
    timeout: float
    terminal_deadline: float
    decision_timeout: float
    delayed_response_sec: float


@dataclass(slots=True)
class TurnTrace:
    """累计单个 Turn 的事件类型、游标和唯一终态。"""

    event_types: list[str]
    event_sequences: list[int]
    terminal: TurnCompletedEvent | None = None

    def record(self, event: ModelEvent) -> None:
        """登记一条已由生产 Protocol Client 校验的事件。"""
        self.event_types.append(event.type)
        if event.event_seq is not None:
            self.event_sequences.append(event.event_seq)
        if isinstance(event, TurnCompletedEvent):
            if self.terminal is not None:
                raise AssertionError("Turn emitted more than one terminal event")
            self.terminal = event


@dataclass(slots=True)
class PendingApprovalRun:
    """保存已经推进到真实待审批事件的活动流。"""

    stream: ModelEventStream
    iterator: AsyncIterator[ModelEvent]
    trace: TurnTrace
    approval: ToolApprovalRequiredEvent


class LiveFaultProxy:
    """只注入响应延迟或单次 SSE 断线的本地透明代理。"""

    def __init__(
        self,
        upstream: str,
        *,
        delayed_approval_response_sec: float = 0.0,
        drop_first_approval_stream: bool = False,
    ) -> None:
        """绑定真实服务地址和单一故障规则。"""
        self.upstream = upstream.rstrip("/")
        self.delayed_approval_response_sec = max(
            0.0,
            float(delayed_approval_response_sec),
        )
        self.drop_first_approval_stream = drop_first_approval_stream
        self.request_counts: dict[str, int] = {}
        self.upstream_statuses: dict[str, list[int]] = {}
        self.stream_drops = 0
        self.approval_response_forwarded = asyncio.Event()
        self.stream_dropped = asyncio.Event()
        self._client = httpx.AsyncClient(timeout=None, trust_env=False)
        self._listener: socket.socket | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._base_url = ""
        self._app = Starlette(routes=[
            Route("/{path:path}", self._forward, methods=["GET", "POST"]),
        ])

    @property
    def base_url(self) -> str:
        """返回代理开始监听后的本地地址。"""
        if not self._base_url:
            raise RuntimeError("fault proxy has not started")
        return self._base_url

    async def start(self) -> None:
        """在随机本地端口启动代理。"""
        if self._server_task is not None:
            return None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        address = listener.getsockname()
        if not isinstance(address, tuple) or len(address) < 2:
            listener.close()
            raise RuntimeError("fault proxy did not receive a TCP address")
        port = address[1]
        if isinstance(port, bool) or not isinstance(port, int):
            listener.close()
            raise RuntimeError("fault proxy did not receive a TCP port")

        server = uvicorn.Server(uvicorn.Config(
            self._app,
            lifespan="off",
            log_level="critical",
            timeout_graceful_shutdown=2,
        ))
        self._listener = listener
        self._server = server
        self._base_url = f"http://127.0.0.1:{port}"
        task = asyncio.create_task(
            server.serve(sockets=[listener]),
            name="live approval fault proxy",
        )
        self._server_task = task
        for _ in range(200):
            if server.started:
                return None
            if task.done():
                await task
                raise RuntimeError("fault proxy stopped during startup")
            await asyncio.sleep(0.01)
        raise TimeoutError("fault proxy did not start")

    async def close(self) -> None:
        """停止代理并关闭它持有的上游连接。"""
        server = self._server
        task = self._server_task
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._client.aclose()
        listener = self._listener
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()
        self._server = None
        self._server_task = None
        self._listener = None

    async def _forward(self, request: Request) -> Response:
        """把请求转发到真实服务，并在指定边界注入一次故障。"""
        path = request.url.path
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.casefold() not in _HOP_BY_HOP_HEADERS
        }
        body = await request.body()
        upstream_url = f"{self.upstream}{path}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"

        if path in {"/mind-chat", "/mind-attach"}:
            return await self._forward_stream(
                path=path,
                url=upstream_url,
                headers=headers,
                body=body,
            )

        response = await self._client.request(
            request.method,
            upstream_url,
            headers=headers,
            content=body,
        )
        self.upstream_statuses.setdefault(path, []).append(response.status_code)
        if path == "/tool-approval":
            self.approval_response_forwarded.set()
            if (
                self.request_counts[path] == 1
                and self.delayed_approval_response_sec > 0
            ):
                await asyncio.sleep(self.delayed_approval_response_sec)
        response_headers = _response_headers(response.headers)
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=response_headers,
        )

    async def _forward_stream(
        self,
        *,
        path: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> StreamingResponse:
        """流式转发 SSE，并在首个审批事件完整经过后断开一次。"""
        upstream_request = self._client.build_request(
            "POST",
            url,
            headers=headers,
            content=body,
        )
        response = await self._client.send(upstream_request, stream=True)
        self.upstream_statuses.setdefault(path, []).append(response.status_code)

        async def chunks() -> AsyncIterator[bytes]:
            observed = b""
            try:
                async for chunk in response.aiter_bytes():
                    observed = (observed + chunk)[-1024:]
                    drop_after_chunk = (
                        path == "/mind-chat"
                        and self.drop_first_approval_stream
                        and self.stream_drops == 0
                        and b"tool.approval_required" in observed
                    )
                    if drop_after_chunk:
                        self.stream_drops += 1
                        self.stream_dropped.set()
                    yield chunk
                    if drop_after_chunk:
                        return
            finally:
                await response.aclose()

        return StreamingResponse(
            chunks(),
            status_code=response.status_code,
            headers=_response_headers(response.headers),
        )


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    """只保留不会破坏本地代理 framing 的响应头。"""
    return {
        name: value
        for name, value in headers.items()
        if name.casefold() not in _HOP_BY_HOP_HEADERS
    }


def _parser() -> argparse.ArgumentParser:
    """创建真实审批矩阵的命令行参数。"""
    parser = argparse.ArgumentParser(
        description=(
            "Verify approval timeout, disconnect, duplicate decisions, and "
            "the following Turn against the configured AppServer."
        ),
    )
    parser.add_argument(
        "--profile",
        help="Use ~/.mind/<profile>.config.toml through ConfigSession.",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--terminal-deadline", type=float, default=45.0)
    parser.add_argument("--decision-timeout", type=float, default=0.5)
    parser.add_argument("--delayed-response-sec", type=float, default=1.0)
    parser.add_argument(
        "--scenario",
        choices=("timeout", "disconnect", "duplicate", "all"),
        default="all",
    )
    return parser


async def _load_config(args: argparse.Namespace) -> LiveApprovalConfig:
    """按正式启动路径读取本机配置并拒绝不完整模型槽位。"""
    positive_values = {
        "timeout": args.timeout,
        "terminal deadline": args.terminal_deadline,
        "decision timeout": args.decision_timeout,
        "delayed response": args.delayed_response_sec,
    }
    for label, value in positive_values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{label} must be positive")
    if args.delayed_response_sec <= args.decision_timeout:
        raise ValueError("delayed response must exceed the decision timeout")

    session = ConfigSession(
        ConfigStore(application_config_path()),
        profile=args.profile,
        workspace=Path.cwd(),
    )
    preferences = Preferences(session)
    await preferences.load_pref()
    domain = await ServiceConfig(session).load_domain()
    if not domain:
        raise ValueError("service.domain is missing from the active Mind config")

    raw_config = preferences.to_config()
    primary = raw_config.get("primary")
    if not isinstance(primary, dict):
        raise ValueError("primary model configuration is missing")
    missing = tuple(
        field
        for field in ("kind", "model", "apikey")
        if not str(primary.get(field) or "").strip()
    )
    if missing:
        raise ValueError(
            "primary model configuration is missing required fields: "
            + ", ".join(missing)
        )

    frozen_config = freeze_json(raw_config, field_name="preferences")
    if not isinstance(frozen_config, Mapping):
        raise TypeError("active Mind preferences must be an object")
    pref_config = thaw_object(frozen_config, field_name="preferences")
    service_endpoints.configure(domain)
    return LiveApprovalConfig(
        domain=domain,
        pref_config=pref_config,
        timeout=float(args.timeout),
        terminal_deadline=float(args.terminal_deadline),
        decision_timeout=float(args.decision_timeout),
        delayed_response_sec=float(args.delayed_response_sec),
    )


def _environment_snapshot() -> dict[str, JsonValue]:
    """构造只用于服务端审批动作绑定的本地环境快照。"""
    workspace = str(Path.cwd().resolve())
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )
    return {
        "snapshot_id": f"envsnap_{short_uid(20)}",
        "source": "client",
        "captured_at": captured_at,
        "environment_id": "local",
        "cwd": workspace,
        "status": "available",
        "status_detail": None,
        "shell": {
            "name": "powershell",
            "syntax": "powershell",
            "executable": "pwsh.exe",
            "prefix": ["pwsh.exe", "-NoProfile", "-Command"],
            "source": "live_approval_runtime",
        },
        "workspace": {
            "root": workspace,
            "allowed_roots": [workspace],
            "source": "client",
        },
        "tools": {},
        "providers": {},
        "extensions": {},
    }


def _approval_request(
    config: LiveApprovalConfig,
    *,
    cid: str,
    sid: str,
    turn_id: str,
    scenario: str,
) -> ModelStreamRequest:
    """构造必然请求一次命令工具且不要求真实执行的模型输入。"""
    tool = {
        "name": "shell_command",
        "description": "Run one local shell command requested by the user.",
        "inputSchema": shell_command_input_schema(),
        "meta": {
            "client_builtin": True,
            "domain": "coding",
            "class": "shell",
        },
    }
    return ModelStreamRequest(
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        pref_config=config.pref_config,
        message=(
            "Call shell_command exactly once with command "
            f"`Write-Output APPROVAL-{scenario.upper()}`. "
            "Do not use any other command. If approval is declined, do not "
            "retry the tool and reply with only DECLINED."
        ),
        tools=(tool,),
        environment_snapshot=_environment_snapshot(),
        metadata={
            "origin": "live_approval_runtime",
            "scenario": scenario,
        },
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "untrusted",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
            "tool_choice": "auto",
        },
        timeout=config.timeout,
    )


def _next_turn_request(
    config: LiveApprovalConfig,
    *,
    cid: str,
    sid: str,
    scenario: str,
) -> ModelStreamRequest:
    """构造同一 Session 中用于验证审批后执行门的普通输入。"""
    return ModelStreamRequest(
        cid=cid,
        sid=sid,
        turn_id=f"turn_{short_uid(20)}",
        pref_config=config.pref_config,
        message=f"Reply with only NEXT-{scenario.upper()}.",
        tools=(),
        metadata={
            "origin": "live_approval_runtime",
            "scenario": f"{scenario}_next_turn",
        },
        options={
            "permissions": {
                "sandbox_mode": "read-only",
                "approval_policy": "never",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
            "tool_choice": "none",
        },
        timeout=config.timeout,
    )


async def _open_pending_approval(
    client: MindChatProtocolClient,
    request: ModelStreamRequest,
    *,
    on_recovery_status: RecoveryStatusCallback | None = None,
    on_approval_snapshot: ApprovalSnapshotCallback | None = None,
) -> PendingApprovalRun:
    """推进真实模型流直到首个 pending 审批事件。"""
    stream = client.stream(
        request,
        on_recovery_status=on_recovery_status,
        on_approval_snapshot=on_approval_snapshot,
    )
    iterator = stream.__aiter__()
    trace = TurnTrace(event_types=[], event_sequences=[])
    async for event in iterator:
        trace.record(event)
        if isinstance(event, ToolApprovalRequiredEvent) and event.status == "pending":
            return PendingApprovalRun(
                stream=stream,
                iterator=iterator,
                trace=trace,
                approval=event,
            )
        if isinstance(event, TurnCompletedEvent):
            raise AssertionError("Turn completed before producing an approval")
    raise AssertionError("Turn stream ended before producing an approval")


async def _finish_turn(run: PendingApprovalRun) -> TurnCompletedEvent:
    """消费审批后的剩余事件并验证唯一权威终态。"""
    async for event in run.iterator:
        run.trace.record(event)
    terminal = run.trace.terminal
    if terminal is None:
        raise AssertionError("approval Turn ended without turn.completed")
    _assert_settled_stream(run.stream, run.trace, terminal)
    return terminal


async def _consume_turn(stream: ModelEventStream) -> tuple[TurnTrace, TurnCompletedEvent]:
    """完整消费一轮普通输入并验证终态与游标。"""
    trace = TurnTrace(event_types=[], event_sequences=[])
    async for event in stream:
        trace.record(event)
    terminal = trace.terminal
    if terminal is None:
        raise AssertionError("Turn ended without turn.completed")
    _assert_settled_stream(stream, trace, terminal)
    return trace, terminal


def _assert_settled_stream(
    stream: ModelEventStream,
    trace: TurnTrace,
    terminal: TurnCompletedEvent,
) -> None:
    """统一验证事件严格单调、终态闭流和客户端游标。"""
    if trace.event_sequences != sorted(set(trace.event_sequences)):
        raise AssertionError("Turn event sequence is not strictly increasing")
    if not trace.event_sequences or trace.event_sequences[-1] != terminal.last_event_seq:
        raise AssertionError("turn.completed does not close the event cursor")
    if stream.end_reason != "settled":
        raise AssertionError(f"Turn stream ended as {stream.end_reason!r}")
    if stream.last_event_seq != terminal.last_event_seq:
        raise AssertionError("Protocol Client cursor did not reach turn.completed")


async def _verify_next_turn(
    config: LiveApprovalConfig,
    client: MindChatProtocolClient,
    *,
    cid: str,
    sid: str,
    scenario: str,
    previous_terminal_seq: int,
) -> TurnCompletedEvent:
    """提交下一条普通输入并确认执行门和 Session cursor 已释放。"""
    request = _next_turn_request(
        config,
        cid=cid,
        sid=sid,
        scenario=scenario,
    )
    trace, terminal = await asyncio.wait_for(
        _consume_turn(client.stream(request)),
        timeout=config.timeout + config.terminal_deadline,
    )
    if terminal.status != "completed":
        raise AssertionError(
            f"next Turn ended as {terminal.status}: {terminal.error or 'no error'}"
        )
    if not trace.event_sequences or trace.event_sequences[0] <= previous_terminal_seq:
        raise AssertionError("next Turn did not advance the Session cursor")
    return terminal


async def _decision(
    run: PendingApprovalRun,
    decision: str,
    *,
    request_id: str,
    timeout: float = 60.0,
) -> None:
    """使用审批事件的完整正式坐标提交决定。"""
    approval = run.approval
    await post_tool_approval(
        approval.cid,
        approval.sid,
        approval.call_id,
        approval.approval_id,
        decision,
        turn_id=approval.turn_id,
        kind=approval.kind,
        request_id=request_id,
        timeout=timeout,
    )


async def _timeout_scenario(config: LiveApprovalConfig) -> None:
    """验证首个审批响应超时后以同一 request id 取得重复回执。"""
    cid = new_cid("cid")
    sid = new_sid(cid, "sid")
    client = MindChatProtocolClient()
    request = _approval_request(
        config,
        cid=cid,
        sid=sid,
        turn_id=f"turn_{short_uid(20)}",
        scenario="timeout",
    )
    run = await asyncio.wait_for(
        _open_pending_approval(client, request),
        timeout=config.timeout + config.terminal_deadline,
    )
    proxy = LiveFaultProxy(
        config.domain,
        delayed_approval_response_sec=config.delayed_response_sec,
    )
    await proxy.start()
    request_id = new_request_id("approval_timeout_live")
    try:
        service_endpoints.configure(proxy.base_url)
        await _decision(
            run,
            "decline",
            request_id=request_id,
            timeout=config.decision_timeout,
        )
    finally:
        service_endpoints.configure(config.domain)
        await proxy.close()

    if proxy.request_counts.get("/tool-approval", 0) < 2:
        raise AssertionError("approval timeout did not trigger a same-id retry")
    if proxy.upstream_statuses.get("/tool-approval", []).count(200) < 2:
        raise AssertionError("approval timeout retry did not reach duplicate ACK")
    terminal = await asyncio.wait_for(
        _finish_turn(run),
        timeout=config.timeout + config.terminal_deadline,
    )
    next_terminal = await _verify_next_turn(
        config,
        client,
        cid=cid,
        sid=sid,
        scenario="timeout",
        previous_terminal_seq=terminal.last_event_seq,
    )
    print(
        "PASS approval response timeout: "
        f"decision_posts={proxy.request_counts['/tool-approval']} "
        f"terminal={terminal.status}@{terminal.last_event_seq} "
        f"next=completed@{next_terminal.last_event_seq}"
    )


async def _disconnect_scenario(config: LiveApprovalConfig) -> None:
    """验证审批事件后的 SSE 断线经快照和 attach 恢复。"""
    cid = new_cid("cid")
    sid = new_sid(cid, "sid")
    client = MindChatProtocolClient()
    proxy = LiveFaultProxy(config.domain, drop_first_approval_stream=True)
    await proxy.start()
    recovery_phases: list[str] = []
    snapshots: list[ToolApprovalSnapshot] = []
    snapshot_seen = asyncio.Event()
    next_event: asyncio.Task[ModelEvent] | None = None

    async def recovery_status(
        phase: TransportRecoveryPhase,
        event_seq: int,
    ) -> None:
        """记录生产流恢复阶段，不把通知当作终态。"""
        _ = event_seq
        recovery_phases.append(phase)

    async def approval_snapshot(snapshot: object) -> None:
        """记录重连时服务端返回的权威审批快照。"""
        if not isinstance(snapshot, ToolApprovalSnapshot):
            raise TypeError("approval recovery returned an invalid snapshot")
        snapshots.append(snapshot)
        snapshot_seen.set()

    try:
        service_endpoints.configure(proxy.base_url)
        request = _approval_request(
            config,
            cid=cid,
            sid=sid,
            turn_id=f"turn_{short_uid(20)}",
            scenario="disconnect",
        )
        run = await asyncio.wait_for(
            _open_pending_approval(
                client,
                request,
                on_recovery_status=recovery_status,
                on_approval_snapshot=approval_snapshot,
            ),
            timeout=config.timeout + config.terminal_deadline,
        )
        await asyncio.wait_for(proxy.stream_dropped.wait(), timeout=5.0)
        next_event = asyncio.create_task(
            anext(run.iterator),
            name="live approval reconnect event",
        )
        await asyncio.wait_for(
            snapshot_seen.wait(),
            timeout=config.timeout + config.terminal_deadline,
        )
        snapshot = snapshots[-1]
        matching = tuple(
            item
            for item in snapshot.approvals
            if item.approval_id == run.approval.approval_id
        )
        if len(matching) != 1 or matching[0].status != "pending":
            raise AssertionError("approval snapshot did not preserve pending identity")

        await _decision(
            run,
            "decline",
            request_id=new_request_id("approval_disconnect_live"),
        )
        run.trace.record(await asyncio.wait_for(
            next_event,
            timeout=config.timeout + config.terminal_deadline,
        ))
        terminal = await asyncio.wait_for(
            _finish_turn(run),
            timeout=config.timeout + config.terminal_deadline,
        )
    finally:
        if next_event is not None and not next_event.done():
            next_event.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_event
        service_endpoints.configure(config.domain)
        await proxy.close()

    required_phases = {"reconnecting", "replaying", "caught_up"}
    if not required_phases.issubset(recovery_phases):
        raise AssertionError(
            "approval disconnect missed recovery phases: "
            + ", ".join(sorted(required_phases.difference(recovery_phases)))
        )
    if proxy.request_counts.get("/mind-attach", 0) < 1:
        raise AssertionError("approval disconnect did not attach after snapshot")
    next_terminal = await _verify_next_turn(
        config,
        client,
        cid=cid,
        sid=sid,
        scenario="disconnect",
        previous_terminal_seq=terminal.last_event_seq,
    )
    print(
        "PASS approval SSE disconnect: "
        f"drops={proxy.stream_drops} attach={proxy.request_counts['/mind-attach']} "
        f"terminal={terminal.status}@{terminal.last_event_seq} "
        f"next=completed@{next_terminal.last_event_seq}"
    )


async def _duplicate_scenario(config: LiveApprovalConfig) -> None:
    """验证传输重复、语义重复和两类冲突均不覆盖首个决定。"""
    cid = new_cid("cid")
    sid = new_sid(cid, "sid")
    client = MindChatProtocolClient()
    request = _approval_request(
        config,
        cid=cid,
        sid=sid,
        turn_id=f"turn_{short_uid(20)}",
        scenario="duplicate",
    )
    run = await asyncio.wait_for(
        _open_pending_approval(client, request),
        timeout=config.timeout + config.terminal_deadline,
    )
    first_request_id = new_request_id("approval_duplicate_live")
    semantic_duplicate_id = new_request_id("approval_semantic_duplicate_live")
    conflict_id = new_request_id("approval_conflict_live")

    await _decision(
        run,
        "decline",
        request_id=first_request_id,
    )
    await _decision(
        run,
        "decline",
        request_id=first_request_id,
    )
    await _decision(
        run,
        "decline",
        request_id=semantic_duplicate_id,
    )

    try:
        await _decision(
            run,
            "accept",
            request_id=semantic_duplicate_id,
        )
    except ToolApprovalRequestError as error:
        if error.code != "request_id_conflict" or error.status_code != 409:
            raise AssertionError(
                "request-id reuse did not preserve request_id_conflict"
            ) from error
    else:
        raise AssertionError("request-id reuse overwrote the first approval decision")

    try:
        await _decision(
            run,
            "accept",
            request_id=conflict_id,
        )
    except ToolApprovalRequestError as error:
        if error.code != "approval_decision_conflict" or error.status_code != 409:
            raise AssertionError(
                "conflicting duplicate did not preserve approval_decision_conflict"
            ) from error
    else:
        raise AssertionError("conflicting duplicate overwrote the first approval decision")

    terminal = await asyncio.wait_for(
        _finish_turn(run),
        timeout=config.timeout + config.terminal_deadline,
    )
    next_terminal = await _verify_next_turn(
        config,
        client,
        cid=cid,
        sid=sid,
        scenario="duplicate",
        previous_terminal_seq=terminal.last_event_seq,
    )
    print(
        "PASS duplicate approval decisions: "
        "same_request=duplicate same_decision=duplicate "
        "request_reuse=request_id_conflict changed_decision=approval_decision_conflict "
        f"terminal={terminal.status}@{terminal.last_event_seq} "
        f"next=completed@{next_terminal.last_event_seq}"
    )


async def _verify(config: LiveApprovalConfig, scenario: str) -> None:
    """顺序执行选定真实故障，避免全局服务 endpoint 相互污染。"""
    scenarios = {
        "timeout": _timeout_scenario,
        "disconnect": _disconnect_scenario,
        "duplicate": _duplicate_scenario,
    }
    selected = tuple(scenarios) if scenario == "all" else (scenario,)
    for name in selected:
        operation = scenarios.get(name)
        if operation is None:
            raise ValueError(f"unsupported live approval scenario: {name}")
        await operation(config)


def main() -> None:
    """运行真实审批验收矩阵。"""
    args = _parser().parse_args()
    config = asyncio.run(_load_config(args))
    asyncio.run(_verify(config, args.scenario))


if __name__ == "__main__":
    main()
