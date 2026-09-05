"""使用本机 Mind 配置验证真实 Durable Turn 交互协议。"""

import argparse
import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.ports import ModelCapabilityError
from agent.ports import ModelEventStream
from agent.ports import ProtocolCommandError
from agent.protocol import ModelStreamRequest
from agent.protocol import TurnObservationRequest
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
from protocol.schema.identifiers import (
    new_cid,
    new_request_id,
    new_sid,
    new_submission_id,
    short_uid,
)
from protocol.schema.stream_events import TurnCompletedEvent
from protocol.transport.endpoints import service_endpoints


@dataclass(frozen=True, slots=True)
class LiveVerificationConfig:
    """保存线上验证使用的非敏感参数和已冻结模型配置。"""

    domain: str
    pref_config: Mapping[str, JsonValue]
    timeout: float
    interrupt_ack_deadline: float
    terminal_deadline: float


@dataclass(frozen=True, slots=True)
class TurnObservation:
    """保存一次真实 Turn 的公开事件与终态观测。"""

    turn_id: str
    event_types: tuple[str, ...]
    event_sequences: tuple[int, ...]
    terminal_status: str
    terminal_error: str | None
    terminal_sequence: int
    assistant_text: str


def _parser() -> argparse.ArgumentParser:
    """创建只接受非敏感验证参数的命令行解析器。"""
    parser = argparse.ArgumentParser(
        description=(
            "Verify Durable Turn behavior using the active local Mind config."
        ),
    )
    parser.add_argument(
        "--profile",
        help="Use ~/.mind/<profile>.config.toml through ConfigSession.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--interrupt-ack-deadline",
        type=float,
        default=3.0,
    )
    parser.add_argument(
        "--terminal-deadline",
        type=float,
        default=15.0,
    )
    return parser


async def _load_config(args: argparse.Namespace) -> LiveVerificationConfig:
    """按正式启动路径读取本机配置并拒绝不完整的模型槽位。"""
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    if args.interrupt_ack_deadline <= 0:
        raise ValueError("interrupt ACK deadline must be positive")
    if args.terminal_deadline <= 0:
        raise ValueError("terminal deadline must be positive")

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
    pref_config = thaw_object(
        frozen_config,
        field_name="preferences",
    )
    service_endpoints.configure(domain)
    return LiveVerificationConfig(
        domain=domain,
        pref_config=pref_config,
        timeout=float(args.timeout),
        interrupt_ack_deadline=float(args.interrupt_ack_deadline),
        terminal_deadline=float(args.terminal_deadline),
    )


def _request(
    config: LiveVerificationConfig,
    *,
    cid: str,
    sid: str,
    turn_id: str,
    message: str,
) -> ModelStreamRequest:
    """构造不包含工具副作用的真实模型请求。"""
    return ModelStreamRequest(
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        pref_config=config.pref_config,
        message=message,
        tools=(),
        metadata={"origin": "durable_turn_live_verifier"},
        options={
            "permissions": {
                "sandbox_mode": "read-only",
                "approval_policy": "never",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
        },
        timeout=config.timeout,
    )


async def _consume_turn(
    client: MindChatProtocolClient,
    request: ModelStreamRequest,
    *,
    started: asyncio.Event | None = None,
) -> TurnObservation:
    """消费生产事件流并验证唯一终态和严格递增的事件序号。"""
    return await _consume_stream(
        request.turn_id,
        client.stream(request),
        started=started,
    )


async def _consume_observed_turn(
    client: MindChatProtocolClient,
    request: ModelStreamRequest,
) -> TurnObservation:
    """仅通过 attach 消费一个已由 Queue start 创建的 Turn。"""
    return await _consume_stream(
        request.turn_id,
        client.observe(TurnObservationRequest(
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            timeout=request.timeout,
        )),
    )


async def _consume_stream(
    turn_id: str,
    stream: ModelEventStream,
    *,
    started: asyncio.Event | None = None,
) -> TurnObservation:
    """验证提交流或 attach 流共享的终态、序号和游标契约。"""
    event_types: list[str] = []
    event_sequences: list[int] = []
    terminal_events: list[TurnCompletedEvent] = []
    async for event in stream:
        event_types.append(event.type)
        if event.event_seq is not None:
            event_sequences.append(event.event_seq)
        if event.type == "turn.started" and started is not None:
            started.set()
        if isinstance(event, TurnCompletedEvent):
            terminal_events.append(event)

    if len(terminal_events) != 1:
        raise AssertionError(
            f"{turn_id}: expected one turn.completed, "
            f"observed {len(terminal_events)}"
        )
    if event_sequences != sorted(set(event_sequences)):
        raise AssertionError(
            f"{turn_id}: event sequence is not strictly increasing"
        )
    terminal = terminal_events[0]
    if event_sequences[-1] != terminal.last_event_seq:
        raise AssertionError(
            f"{turn_id}: terminal cursor does not close the stream"
        )
    if stream.end_reason != "settled":
        raise AssertionError(
            f"{turn_id}: stream ended as {stream.end_reason!r}"
        )
    if stream.last_event_seq != terminal.last_event_seq:
        raise AssertionError(
            f"{turn_id}: protocol cursor did not reach terminal"
        )
    return TurnObservation(
        turn_id=turn_id,
        event_types=tuple(event_types),
        event_sequences=tuple(event_sequences),
        terminal_status=terminal.status,
        terminal_error=terminal.error,
        terminal_sequence=terminal.last_event_seq,
        assistant_text=stream.assistant_text,
    )


def _print_observation(label: str, observation: TurnObservation) -> None:
    """输出不含请求配置和凭据的 Turn 验证摘要。"""
    print(
        f"PASS {label}: status={observation.terminal_status} "
        f"cursor={observation.terminal_sequence} "
        f"events={','.join(observation.event_types)}"
    )


async def _verify(config: LiveVerificationConfig) -> None:
    """执行完成、中断、冲突、对账和下一轮 cursor 场景。"""
    cid = new_cid("cid")
    sid = new_sid(cid, "sid")
    client = MindChatProtocolClient()

    normal = await asyncio.wait_for(
        _consume_turn(
            client,
            _request(
                config,
                cid=cid,
                sid=sid,
                turn_id=f"turn_{short_uid(20)}",
                message="Reply with only OK.",
            ),
        ),
        timeout=config.timeout + config.terminal_deadline,
    )
    if normal.terminal_status != "completed":
        raise AssertionError(
            f"normal turn ended as {normal.terminal_status}: "
            f"{normal.terminal_error or 'no error'}"
        )
    _print_observation("normal completion", normal)

    active_turn_id = f"turn_{short_uid(20)}"
    active_request = _request(
        config,
        cid=cid,
        sid=sid,
        turn_id=active_turn_id,
        message=(
            "Before answering, silently inspect this request in detail and "
            "then write a long numbered analysis with at least 100 items."
        ),
    )
    started = asyncio.Event()
    active_task = asyncio.create_task(
        _consume_turn(client, active_request, started=started),
        name="live durable active turn",
    )
    await asyncio.wait_for(started.wait(), timeout=config.timeout)

    queued_request = _request(
        config,
        cid=cid,
        sid=sid,
        turn_id=f"turn_{short_uid(20)}",
        message="Reply with only QUEUED.",
    )
    submission_id = new_submission_id("submission_live")
    client_message_id = f"message_{short_uid(20)}"
    add_request_id = new_request_id("queue_add_live")
    add_receipt = await client.add_queue_submission(
        queued_request,
        submission_id=submission_id,
        client_message_id=client_message_id,
        request_id=add_request_id,
    )

    active_status = await client.get_turn_status(
        cid=cid,
        sid=sid,
        turn_id=active_turn_id,
    )
    if active_status.terminal is not None:
        raise AssertionError(
            "active Turn completed before the Queue execution gate check"
        )

    rejected_start_request_id = new_request_id("queue_start_busy_live")
    try:
        await client.start_queue_submission(
            cid=cid,
            sid=sid,
            submission_id=submission_id,
            request_id=rejected_start_request_id,
        )
    except ProtocolCommandError as error:
        if error.status_code != 409 or error.retryable:
            raise AssertionError(
                "active Turn queue start did not return a deterministic 409"
            ) from error
    else:
        raise AssertionError("queue start succeeded while another Turn was active")
    print(
        "PASS Queue execution gate: start rejected while Turn "
        f"status={active_status.status}"
    )

    duplicate_add_receipt = await client.add_queue_submission(
        queued_request,
        submission_id=submission_id,
        client_message_id=client_message_id,
        request_id=add_request_id,
    )
    if duplicate_add_receipt != add_receipt:
        raise AssertionError("idempotent queue add returned another receipt")
    queue_snapshot = await client.list_queue_submissions(cid=cid, sid=sid)
    if (
        tuple(item.submission_id for item in queue_snapshot.items)
        != (submission_id,)
        or add_receipt.item.position != 1
        or add_receipt.item.turn_id != queued_request.turn_id
    ):
        raise AssertionError("active Turn queue add did not preserve FIFO identity")
    print("PASS active Queue add: one durable item and stable duplicate receipt")

    conflict_request = _request(
        config,
        cid=cid,
        sid=sid,
        turn_id=f"turn_{short_uid(20)}",
        message="This conflicting turn must never start.",
    )
    try:
        await _consume_turn(client, conflict_request)
    except ModelCapabilityError as error:
        status_code = error.details.get("status_code")
        if status_code != 409 or error.code != "turn_already_active":
            raise AssertionError(
                "active Turn conflict did not preserve the service error "
                f"contract: code={error.code!r}, status={status_code!r}"
            ) from error
    else:
        raise AssertionError("a second /mind-chat started before terminal")
    print("PASS execution gate: active Turn rejected the second /mind-chat")

    ack_started = time.perf_counter()
    receipt = await client.interrupt_turn(
        cid=cid,
        sid=sid,
        turn_id=active_turn_id,
        request_id=new_request_id("interrupt_live"),
    )
    ack_elapsed = time.perf_counter() - ack_started
    if receipt.status != "accepted":
        raise AssertionError(f"interrupt ACK status is {receipt.status!r}")
    if ack_elapsed > config.interrupt_ack_deadline:
        raise AssertionError(
            f"interrupt ACK took {ack_elapsed:.3f}s, deadline is "
            f"{config.interrupt_ack_deadline:.3f}s"
        )
    terminal_started = time.perf_counter()
    interrupted = await asyncio.wait_for(
        active_task,
        timeout=config.terminal_deadline,
    )
    terminal_elapsed = time.perf_counter() - terminal_started
    if interrupted.terminal_status != "interrupted":
        raise AssertionError(
            f"interrupted turn ended as {interrupted.terminal_status}: "
            f"{interrupted.terminal_error or 'no error'}"
        )
    print(
        f"PASS interrupt ACK: latency={ack_elapsed:.3f}s; "
        f"terminal latency={terminal_elapsed:.3f}s"
    )
    _print_observation("authoritative interrupted terminal", interrupted)

    status = await client.get_turn_status(
        cid=cid,
        sid=sid,
        turn_id=active_turn_id,
    )
    if status.terminal is None:
        raise AssertionError("terminal /turn/status snapshot is missing")
    if (
        status.status != interrupted.terminal_status
        or status.last_event_seq != interrupted.terminal_sequence
        or status.terminal.last_event_seq != interrupted.terminal_sequence
    ):
        raise AssertionError("/turn/status is not equivalent to turn.completed")
    print("PASS terminal snapshot: /turn/status matches turn.completed")

    reconciliation_id = f"message_{short_uid(20)}"
    reconciliation = await client.reconcile_turn_inputs(
        cid=cid,
        sid=sid,
        turn_id=active_turn_id,
        client_message_ids=(reconciliation_id,),
    )
    classified = (
        reconciliation.committed_ids
        + reconciliation.pending_ids
        + reconciliation.retry_ids
        + reconciliation.unknown_ids
    )
    if classified != (reconciliation_id,):
        raise AssertionError("reconciliation did not classify the input once")
    if (
        not reconciliation.turn_exists
        or reconciliation.terminal is None
        or reconciliation.terminal.last_event_seq
        != interrupted.terminal_sequence
    ):
        raise AssertionError("reconciliation terminal snapshot is inconsistent")
    print("PASS input reconciliation: exactly-one classification with terminal")

    start_request_id = new_request_id("queue_start_live")
    start_receipt = await client.start_queue_submission(
        cid=cid,
        sid=sid,
        submission_id=submission_id,
        request_id=start_request_id,
    )
    duplicate_start_receipt = await client.start_queue_submission(
        cid=cid,
        sid=sid,
        submission_id=submission_id,
        request_id=start_request_id,
    )
    if (
        duplicate_start_receipt != start_receipt
        or start_receipt.turn_id != queued_request.turn_id
    ):
        raise AssertionError("idempotent queue start returned another Turn")
    started_snapshot = await client.list_queue_submissions(cid=cid, sid=sid)
    if started_snapshot.items:
        raise AssertionError("started Queue item remained in the pending snapshot")
    queued_turn = await asyncio.wait_for(
        _consume_observed_turn(client, queued_request),
        timeout=config.timeout + config.terminal_deadline,
    )
    if queued_turn.terminal_status != "completed":
        raise AssertionError(
            f"queued turn ended as {queued_turn.terminal_status}: "
            f"{queued_turn.terminal_error or 'no error'}"
        )
    if queued_turn.event_sequences[0] <= interrupted.terminal_sequence:
        raise AssertionError("Queue attach did not advance the Session cursor")
    _print_observation("Queue start and attach-only observation", queued_turn)

    next_turn = await asyncio.wait_for(
        _consume_turn(
            client,
            _request(
                config,
                cid=cid,
                sid=sid,
                turn_id=f"turn_{short_uid(20)}",
                message="Reply with only NEXT.",
            ),
        ),
        timeout=config.timeout + config.terminal_deadline,
    )
    if next_turn.terminal_status != "completed":
        raise AssertionError(
            f"next turn ended as {next_turn.terminal_status}: "
            f"{next_turn.terminal_error or 'no error'}"
        )
    if next_turn.event_sequences[0] <= queued_turn.terminal_sequence:
        raise AssertionError(
            "next Turn did not advance past the Queue terminal cursor"
        )
    _print_observation("next Turn cursor continuity", next_turn)


async def _main() -> int:
    """加载配置并返回适合自动化调用的退出码。"""
    args = _parser().parse_args()
    try:
        config = await _load_config(args)
        print(f"CONFIG service.domain={config.domain} model_credentials=ready")
        await _verify(config)
    except ProtocolCommandError as error:
        print(
            f"FAIL {type(error).__name__}: {error}; code={error.code}; "
            f"retryable={error.retryable}; details={error.details}"
        )
        return 1
    except (
        AssertionError,
        ModelCapabilityError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        print(f"FAIL {type(error).__name__}: {error}")
        return 1
    print("PASS durable Turn live verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
