"""从真实终端验收源码传输 adapter，或启动仅含故障 fixture 的源码 TUI。"""

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import time

import tomlkit

from dataclasses import (
    asdict,
    dataclass,
    field,
)
from datetime import (
    datetime,
    timezone,
)
from importlib.metadata import version
from pathlib import Path

from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.settings import NormalizedMcpServer
from infrastructure.mcp.values import slugify_mcp_name
from tests.external_mcp.fixtures import (
    FixtureMode,
    FixtureReply,
    FixtureSpec,
    client_arguments,
    close_fixture_process,
    read_facts,
    remote_fixture,
    wait_for_fact,
)


@dataclass
class CaseEvidence:
    """仅保存本机故障服务身份和核验结果，不保存工具正文或用户配置。"""
    name: str
    status: str = "running"
    pid: int | None = None
    process_close_recorded: bool = False
    remote_exit_code: int | None = None
    initialize_attempts: int = 0
    peer_ports: list[int] = field(default_factory=list)
    tool_calls: int = 0
    cancelled: bool = False
    owners_after_close: int = 0
    elapsed_sec: float = 0


def require(condition: bool, code: str) -> None:
    """以固定步骤标识拒绝将未通过的场景写成成功。"""
    if not condition:
        raise RuntimeError(code)


def check_reply(result: mcp_types.CallToolResult, expected: str) -> None:
    """校验真实工具返回值及一次调用事实，不打印大消息正文。"""
    require(not result.isError and len(result.content) == 1, "tool_result_failed")
    item = result.content[0]
    if not isinstance(item, mcp_types.TextContent):
        raise RuntimeError("unexpected_tool_result")
    reply = FixtureReply.model_validate_json(item.text)
    require(reply.value == expected and reply.call_count == 1, "tool_result_mismatch")


async def check_stdio(spec: FixtureSpec, evidence: CaseEvidence) -> None:
    """使用正式连接 owner 拉起服务，核对超限拒绝或大消息成功及退出事实。"""
    group = ExternalMcpGroup()
    task = asyncio.create_task(group.start_service({
        "name": spec.name, "transport": "stdio", "command": sys.executable,
        "args": list(spec.arguments()), "cwd": str(spec.repository), "startup_timeout_sec": 10,
    }))
    try:
        if spec.mode == "handshake-timeout":
            await wait_for_fact(spec.facts_path, "fault.injected")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                evidence.cancelled = True
            require(evidence.cancelled, "stdio_cancellation_ignored")
        else:
            connected = await task
            require(connected == (spec.mode == "large-response"), "stdio_connection_result")
            if connected:
                check_reply(await group.call_tool(f"mcp__{slugify_mcp_name(spec.name)}__ping", {}), "汉🙂" * 300_000)
            else:
                require(not group.tools, "oversized_catalog_published")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await group.close()
    if spec.mode != "handshake-timeout":
        await wait_for_fact(spec.facts_path, "process.closed")
    evidence.owners_after_close = len(group.owned_keys)
    require(evidence.owners_after_close == 0, "stdio_owner_retained")


async def check_http(spec: FixtureSpec, evidence: CaseEvidence, *, cancel: bool = False, deadline: bool = False) -> None:
    """在真实 TCP 连接上检验重试、退出与调用不重放，不替换 HTTP transport。"""
    group = ExternalMcpGroup()
    async with remote_fixture(spec) as remote:
        server: NormalizedMcpServer = {
            "name": spec.name, "transport": "streamable_http", "url": remote.url,
            "startup_timeout_sec": 0.7 if deadline else 10,
        }
        task = asyncio.create_task(group.start_service(server))
        try:
            if cancel:
                await wait_for_fact(spec.facts_path, "request.completed")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    evidence.cancelled = True
                require(evidence.cancelled, "cancellation_ignored")
            else:
                connected = await task
                require(connected == (spec.mode in {"http-recover", "http-call-failure"}), "http_connection_result")
                if spec.mode == "http-recover":
                    check_reply(await group.call_tool(f"mcp__{slugify_mcp_name(spec.name)}__ping", {}), "ok")
                elif spec.mode == "http-call-failure":
                    try:
                        await group.call_tool(f"mcp__{slugify_mcp_name(spec.name)}__ping", {})
                    except McpError:
                        pass
                    else:
                        raise RuntimeError("tool_failure_not_reported")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await group.close()
        await asyncio.sleep(0.3)
        facts = read_facts(spec.facts_path)
        attempts = [fact for fact in facts if fact.event == "initialize.received"]
        evidence.initialize_attempts = len(attempts)
        evidence.peer_ports = [fact.peer_port for fact in attempts if fact.peer_port is not None]
        expected = 1 if cancel or spec.mode in {"http-unauthorized", "http-protocol-error", "http-call-failure"} else 2 if deadline else 3
        require(len(attempts) == expected, "unexpected_initialize_count")
        require(len(set(evidence.peer_ports)) == expected, "connection_reused_across_attempts")
        evidence.owners_after_close = len(group.owned_keys)
        require(not group.owned_keys and not group.tools, "http_resources_retained")
        if spec.mode == "http-recover":
            await wait_for_fact(spec.facts_path, "session.closed")
        if spec.mode in {"http-recover", "http-call-failure"}:
            require(sum(fact.event == "tool.started" for fact in read_facts(spec.facts_path)) == 1, "tool_replayed")
    evidence.remote_exit_code = remote.process.returncode
    require(evidence.remote_exit_code is not None, "remote_process_retained")


async def run_checks(directory: Path, repository: Path, *, stdio_only: bool = False) -> None:
    """串行运行实际故障服务并独占创建报告，失败证据也保留供复核。"""
    cases: list[CaseEvidence] = []
    report = directory / "report.json"
    try:
        scenarios: tuple[tuple[str, FixtureMode], ...] = (
            ("oversize_line", "oversize-line"), ("oversize_unframed", "oversize-unframed"),
            ("large_response", "large-response"), ("stdio_cancel", "handshake-timeout"),
            ("http_recover", "http-recover"),
            ("http_exhausted", "http-exhausted"), ("http_unauthorized", "http-unauthorized"),
            ("http_protocol_error", "http-protocol-error"), ("http_call_failure", "http-call-failure"),
            ("http_cancel", "http-exhausted"), ("http_deadline", "http-exhausted"),
        )
        for name, mode in scenarios:
            if stdio_only and name.startswith("http_"):
                continue
            evidence = CaseEvidence(name)
            cases.append(evidence)
            started = time.monotonic()
            spec = FixtureSpec(
                directory, name, "streamable_http" if name.startswith("http_") else "stdio",
                mode, repository=repository,
            )
            try:
                if spec.transport == "stdio":
                    await check_stdio(spec, evidence)
                else:
                    await check_http(spec, evidence, cancel=name == "http_cancel", deadline=name == "http_deadline")
            except BaseException:
                evidence.status = "failed"
                raise
            else:
                evidence.status = "passed"
            finally:
                evidence.elapsed_sec = round(time.monotonic() - started, 3)
                facts = read_facts(spec.facts_path)
                evidence.pid = facts[0].pid if facts else None
                evidence.process_close_recorded = any(fact.event == "process.closed" for fact in facts)
                evidence.tool_calls = sum(fact.event == "tool.started" for fact in facts)
                print(f"{name}: {evidence.status} ({evidence.elapsed_sec}s)", flush=True)
    finally:
        with report.open("x", encoding="utf-8") as stream:
            json.dump({
                "time_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                "entry_kind": "source-runtime-adapter", "terminal": sys.stdin.isatty(),
                "event_loop": type(asyncio.get_running_loop()).__name__,
                "mcp_sdk": version("mcp"), "cases": [asdict(case) for case in cases],
            }, stream, ensure_ascii=False, indent=2)
        print(f"Evidence: {report}", flush=True)


async def run_client(directory: Path, repository: Path) -> None:
    """启动隔离故障服务及源码 TUI，MCP 表完整覆盖而不修改日常配置。"""
    async with contextlib.AsyncExitStack() as stack:
        remote = await stack.enter_async_context(remote_fixture(FixtureSpec(
            directory, "retry", "streamable_http", "http-recover", repository=repository,
        )))
        servers = tomlkit.table()
        stdio_cases: tuple[tuple[str, FixtureMode], ...] = (("large", "large-response"), ("limit", "oversize-unframed"))
        for name, mode in stdio_cases:
            spec = FixtureSpec(directory, name, mode=mode, repository=repository)
            servers[name] = {
                "command": sys.executable, "args": list(spec.arguments()), "cwd": str(repository),
                "startup_timeout_sec": 10,
            }
        servers["retry"] = {"url": remote.url, "startup_timeout_sec": 10}
        document = tomlkit.document()
        document["mcp_servers"] = servers
        path = directory / "config.toml"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(tomlkit.dumps(document))
        process = await asyncio.create_subprocess_exec(*client_arguments(path, repository=repository), cwd=directory)
        try:
            await process.wait()
        finally:
            await close_fixture_process(process)
        require(process.returncode == 0, "source_client_exit_failed")
    print(f"Source TUI exited; fixture PID {remote.process.pid} exit={remote.process.returncode}", flush=True)


def main() -> None:
    """只接受新产物目录，避免覆盖已有验收记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--selector-stdio", action="store_true", help="Exercise Windows SDK fallback with a real selector event loop.")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    repository = args.repository.resolve()
    if args.selector_stdio:
        if sys.platform != "win32" or args.client:
            parser.error("--selector-stdio requires Windows and cannot be combined with --client")
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run_checks(directory, repository, stdio_only=True))
    else:
        asyncio.run(run_client(directory, repository) if args.client else run_checks(directory, repository))


if __name__ == "__main__":
    main()
