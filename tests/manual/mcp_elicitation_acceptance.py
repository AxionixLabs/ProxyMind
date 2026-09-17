"""在真实终端从源码验收生产 TUI、交互队列和真实 MCP SDK 服务。"""

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import typing
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import JsonValue

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.domain.mcp_elicitation import McpInvocation
from frontends.cli.frontend import resolve_cli_frontend
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.settings import normalize_mcp_servers
from observability import (
    add_file_sink,
    reset_sinks,
)
from tests.external_mcp.fixtures import (
    FixtureReply,
    FixtureSpec,
    read_facts,
    remote_fixture,
    wait_for_fact,
)


async def wait_until(predicate: typing.Callable[[], bool]) -> None:
    """等待生产界面完成展示或清理，不以固定延迟代替验收。"""
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.02)


async def run(directory: Path, repository: Path, *, extended: bool = False) -> dict[str, JsonValue]:
    """按顺序验收填表、拒绝、取消、并行请求、浏览器和调用取消后的恢复。"""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Acceptance requires a real terminal")
    stdio = FixtureSpec(directory, "input", mode="elicitation", repository=repository)
    http = FixtureSpec(directory, "website", transport="streamable_http", mode="elicitation", repository=repository)
    frontend = resolve_cli_frontend("tui")
    runtime = frontend.interaction
    if not isinstance(runtime, TuiRuntime):
        raise TypeError("Expected the production TUI frontend")
    coordinator = ApprovalCoordinator(runtime)
    group = ExternalMcpGroup(elicitation=coordinator)
    cases: list[str] = []
    activity: list[bool] = []

    async def observe_activity(active: bool) -> None:
        """只记录交互批次的暂停和恢复，不保存输入正文。"""
        activity.append(active)

    await runtime.bind_approval_activity(observe_activity)
    async with contextlib.AsyncExitStack() as stack:
        remote = await stack.enter_async_context(remote_fixture(http))
        servers = normalize_mcp_servers({
            "input": {"command": sys.executable, "args": list(stdio.arguments()), "cwd": str(repository), "tool_timeout_sec": 120},
            "website": {"url": remote.url, "tool_timeout_sec": 120},
        })
        await runtime.open()
        try:
            assert await group.start(servers) == 2

            async def invoke(label: str, value: str, server: str = "input") -> list[JsonValue]:
                """调用真实服务并解析测试专用回显，只将通过的用例名写入报告。"""
                result = await group.call_tool(f"mcp__{server}__ping", {"value": value},
                    invocation=McpInvocation("acceptance", "turn", label, "root"))
                assert not result.isError
                reply = FixtureReply.model_validate_json(result.content[0].text)
                await wait_until(lambda: coordinator.snapshot.unresolved_count == 0 and not runtime.screen.menu.active and not runtime.screen.approval.active)
                return [json.loads(item) for item in reply.value.split(";")]

            answer = await invoke("form-accept", "Set Name to Acceptance and Count to 3, then Submit")
            assert answer == [{"action": "accept", "content": {"name": "Acceptance", "count": 3, "confirmed": False, "color": "blue"}}]
            cases.append("form_edit_review_accept")
            assert await invoke("form-decline", "Choose Decline") == [{"action": "decline"}]
            cases.append("form_decline")
            assert await invoke("form-cancel", "Press Esc to cancel") == [{"action": "cancel"}]
            cases.append("form_cancel")
            assert await invoke("parallel", "parallel") == [{"action": "decline"}, {"action": "cancel"}]
            cases.append("parallel_decline_then_cancel")
            endpoint = urlsplit(remote.url)
            target = f"{endpoint.scheme}://{endpoint.netloc}/elicitation-page"
            assert await invoke("url-decline", f"url:{target}", "website") == [{"action": "decline"}]
            assert not any(fact.event == "browser.opened" for fact in read_facts(http.facts_path))
            cases.append("url_decline_no_navigation")
            assert await invoke("url-accept", f"url:{target}", "website") == [{"action": "accept"}]
            await wait_for_fact(http.facts_path, "browser.opened")
            cases.append("url_accept_real_browser")

            cancelled_call = asyncio.create_task(invoke("call-cancel", "Call will be cancelled after this form appears"))
            try:
                await wait_until(lambda: runtime.screen.menu.active)
                await asyncio.sleep(2)
                cancelled_call.cancel()
                try:
                    await cancelled_call
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Expected call cancellation")
            finally:
                cancelled_call.cancel()
                await asyncio.gather(cancelled_call, return_exceptions=True)
            await wait_until(lambda: coordinator.snapshot.unresolved_count == 0 and not runtime.screen.menu.active and not runtime.screen.approval.active)
            cases.append("call_cancel_cleans_surface")
            assert await invoke("recovery", "Choose Decline after cancellation", "website") == [{"action": "decline"}]
            cases.append("other_connection_recovers")
            if extended:
                answers = await invoke("ui-matrix", "ui-matrix", "website")
                assert answers == [{"action": "accept", "content": {
                    "notes": "排查输入\n第二行", "tags": ["backend", "storage"], "ratio": 0.75, "empty": "", "long": "界面预览" * 55,
                }}]
                cases.append("unicode_multiline_multiselect_number_empty_long")
        finally:
            await group.close()
            await coordinator.close()
            await runtime.close()
    assert not group.owned_keys and coordinator.snapshot.unresolved_count == 0
    assert activity and activity == [value for _ in range(len(activity) // 2) for value in (True, False)]
    facts = (*read_facts(stdio.facts_path), *read_facts(http.facts_path))
    tool_calls = 9 if extended else 8
    assert len([fact for fact in facts if fact.event == "tool.started"]) == tool_calls
    assert len([fact for fact in facts if fact.event == "session.opened"]) == len([fact for fact in facts if fact.event == "session.closed"]) == 2
    return {"cases": cases, "tool_calls": tool_calls, "closed_sessions": 2, "pids": sorted({fact.pid for fact in facts}),
        "tty": True, "platform": platform.system(), "scope": "production TUI and MCP components, local services; no model or AppServer", "passed": True}


def main() -> None:
    """创建全新验收目录并把结果保存为可复核的 JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    reset_sinks()
    add_file_sink(str(directory / "diagnostics.log"), level="DEBUG", output_format="{message}", encoding="utf-8", enqueue=False)
    report: dict[str, JsonValue] = {"passed": False}
    try:
        report.update(asyncio.run(run(directory, args.repository.resolve(), extended=args.extended)))
    finally:
        with (directory / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
