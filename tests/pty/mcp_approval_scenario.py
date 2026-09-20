"""在原生终端内连接独立 MCP 服务，验证工具审批、授权复用和资源释放。"""

import argparse
import asyncio
import contextlib
import sys
import typing
from dataclasses import replace
from pathlib import Path

from mcp import types as mcp_types

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
from agent.application.approvals.mcp import authorize_mcp_tool_call
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.policies import preset_permissions
from agent.protocol.json_value import ThawedJsonValue
from agent.stores.approvals.facts import InMemoryApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore
from frontends.interaction.contracts import PromptContext
from frontends.terminal.capabilities import (
    TerminalTheme,
    detect_terminal_capabilities,
)
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.approval_policy import ConfigMcpPersistentApprovalStore
from infrastructure.mcp.composite_session import CompositeToolSession
from observability import reset_sinks
from tests.external_mcp.fixtures import (
    FixtureReply,
    FixtureSpec,
    FixtureTransport,
    remote_fixture,
    write_config,
)
from tests.pty.external_mcp_scenario import McpScenario
from tests.pty.tui_scenario import ScenarioFacts


async def wait_until(predicate: typing.Callable[[], bool]) -> None:
    """在固定时限内等待跨进程验收握手。"""
    async with asyncio.timeout(40):
        while not predicate():
            await asyncio.sleep(0.025)


async def observe(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """发布真实选择、焦点、尺寸及详情页状态，不替换产品渲染。"""
    while True:
        card = runtime.screen.approval
        facts.set_detail("card_active", card.state is not None)
        facts.set_detail("card_call", card.state.presentation.context.call_id if card.state is not None else None)
        facts.set_detail("selected", card.selected_index)
        facts.set_detail("pager_active", runtime.screen.static_pager.active)
        facts.set_detail("width", runtime.terminal_width)
        facts.set_detail("height", runtime.terminal_height)
        facts.set_detail("render_counter", runtime.screen.application.render_counter)
        facts.set_detail("draft", runtime.screen.input.buffer.text)
        facts.write()
        await asyncio.sleep(0.03)


async def call_tool(
    driver: McpScenario, coordinator: DomainApprovalCoordinator, index: int,
) -> dict[str, ThawedJsonValue]:
    """通过产品目录、审批门和独立服务执行一次调用。"""
    with driver.owner.use_tools() as group:
        if group is None:
            raise RuntimeError("MCP tool scope is unavailable")
        session = CompositeToolSession(external_group=group)
        name = next(name for name in group.tools if name.endswith("__ping"))
        arguments = {"value": "TTY\n  参数摘要 " + "e\u0301" * 70}
        descriptor = session.mcp_approval_descriptor(name, arguments)
        if descriptor is None:
            raise RuntimeError("MCP approval descriptor is unavailable")
        turn = TurnContext.create(
            agent=AgentContext.root("sid-tty-mcp"), cid="cid-tty-mcp", sid="sid-tty-mcp",
            source="test", pref_config={}, cwd=str(Path.cwd()),
            permissions=preset_permissions("auto"), turn_id=f"turn-tty-{index}",
            approval_coordinator=coordinator,
        )
        authorization = await authorize_mcp_tool_call(
            turn, coordinator=coordinator, call_id=f"call-{index}",
            descriptor=descriptor, arguments=arguments,
        )
        reply_value: ThawedJsonValue = None
        if authorization.allowed:
            result = await session.call_tool(name, arguments, call_id=f"call-{index}", turn_context=turn)
            if result.isError or not result.content or not isinstance(result.content[0], mcp_types.TextContent):
                raise AssertionError("approved MCP call did not succeed")
            reply = FixtureReply.model_validate_json(result.content[0].text)
            assert reply.value == arguments["value"]
            reply_value = {"call_count": reply.call_count, "pid": reply.pid, "session_id": reply.session_id}
        return {
            "allowed": authorization.allowed, "reason": authorization.reason,
            "decision": authorization.outcome.decision if authorization.outcome is not None else None,
            "reply": reply_value,
        }


async def execute(transport: FixtureTransport, path: Path, theme: str) -> None:
    """拥有独立配置、原生 TUI、审批核心和 MCP 连接，并统一关闭。"""
    reset_sinks()
    facts = ScenarioFacts(path, f"approval-{transport}")
    directory = path.parent / "services"
    repository = Path.cwd()
    async with contextlib.AsyncExitStack() as resources:
        remote = None
        if transport != "stdio":
            remote = await resources.enter_async_context(remote_fixture(
                FixtureSpec(directory, "approval", transport, repository=repository),
            ))
        config_path = write_config(directory, () if remote is None else (remote,), repository=repository)
        store = ConfigStore(config_path)
        source = store.read_raw()["mcp_servers"]["A" if remote is None else "approval"]
        store.update({("mcp_servers",): {"approval": {
            **source, "enabled": True, "default_tools_approval_mode": "prompt", "optional_startup_wait_sec": 0,
        }}})
        config = ConfigSession(store, workspace=directory)
        capabilities = detect_terminal_capabilities(input_stream=sys.stdin, output_stream=sys.stdout)
        capabilities = replace(capabilities, theme=TerminalTheme(
            foreground=(0, 0, 0) if theme == "light" else (238, 238, 238),
            background=(255, 255, 255) if theme == "light" else (17, 17, 17),
        ))
        runtime = TuiRuntime(input_obj=create_tui_input(sys.stdin), terminal_capabilities=capabilities)
        await runtime.open()
        driver = McpScenario(runtime, config, facts, animate=False)
        coordinator = DomainApprovalCoordinator(
            ApprovalCoordinator(runtime), fact_store=InMemoryApprovalFactStore(),
            grant_store=InMemorySessionGrantStore(),
            persistent_mcp_approvals=ConfigMcpPersistentApprovalStore(config),
        )
        reader = asyncio.create_task(runtime.read_message(PromptContext(model="fixture-model")))
        observer = asyncio.create_task(observe(runtime, facts))
        try:
            await driver.owner.start()
            facts.set_detail("services", [{
                "key": service.config_key, "state": service.state, "error": service.connection_error,
            } for service in driver.owner.snapshot.services])
            await wait_until(lambda: any(service.state == "ready" for service in driver.owner.snapshot.services))
            facts.set_detail("tty", {"stdin": sys.stdin.isatty(), "stdout": sys.stdout.isatty()})
            facts.stage = "ready"
            await wait_until(lambda: runtime.screen.input.buffer.text == "draft remains")
            first = await call_tool(driver, coordinator, 1)
            facts.set_detail("first", first)
            facts.stage = "first_done"
            await wait_until(path.with_suffix(".repeat").exists)
            if first["decision"] == "acceptAndRemember":
                await driver.owner.restart()
                await wait_until(lambda: any(service.state == "ready" for service in driver.owner.snapshot.services))
            facts.stage = "repeating"
            facts.set_detail("second", await call_tool(driver, coordinator, 2))
            facts.set_detail("config", store.read_raw()["mcp_servers"])
            facts.set_detail("composer_submitted", reader.done())
            facts.stage = "complete"
            await wait_until(path.with_suffix(".ack").exists)
        finally:
            reader.cancel()
            observer.cancel()
            await asyncio.gather(reader, observer, return_exceptions=True)
            await coordinator.close()
            await driver.owner.close()
            await runtime.close()
            facts.set_detail("owner_released", driver.owner.current is None)
            facts.set_detail("pending_mcp_tasks", [
                task.get_name() for task in asyncio.all_tasks()
                if task.get_name().startswith("external MCP ") and not task.done()
            ])
            facts.write()
    facts.set_detail("remote_released", remote is None or remote.process.returncode is not None)
    facts.write()
    print("PTY MCP APPROVAL CLOSED", flush=True)


def main() -> None:
    """解析独立 MCP 审批的传输和验收产物位置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", choices=("stdio", "streamable_http", "sse"))
    parser.add_argument("facts", type=Path)
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    arguments = parser.parse_args()
    asyncio.run(execute(arguments.transport, arguments.facts, arguments.theme))


if __name__ == "__main__":
    main()
