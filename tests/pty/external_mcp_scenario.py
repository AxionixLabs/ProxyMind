"""持有原生终端验收的真实 MCP 资源，按产品命令链执行输入并发布脱敏事实。"""

import argparse
import asyncio
import contextlib
import hashlib
import sys
from collections.abc import Awaitable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mcp import types as mcp_types

from agent.domain.policies import preset_permissions
from agent.harness.mcp.owner import McpRuntimeOwner
from agent.ports.mcp_runtime import McpRuntimeContext
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    StyledBlock,
    Viewport,
)
from agent.ports.tool_runtime import ExternalToolGroupPort
from agent.protocol.json_value import ThawedJsonValue
from frontends.interaction.contracts import PromptContext
from frontends.runtime import FrontendActivity
from frontends.terminal.capabilities import detect_terminal_capabilities
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.contracts.text import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.session.dispatch import (
    DispatchAction,
    TuiCommandDispatcher,
)
from frontends.tui.session.state import TuiSessionState
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from observability import reset_sinks
from tests.external_mcp.fixtures import (
    FixtureReply,
    FixtureSpec,
    FixtureTransport,
    remote_fixture,
    write_config,
)
from tests.pty.tui_scenario import ScenarioFacts


class RecordedApplication(ApplicationSink):
    """记录实际提交的应用视图，同时交付产品正文 renderer。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self.sink = TuiApplicationSink(runtime)
        self.views: list[dict[str, ThawedJsonValue]] = []

    @property
    def viewport(self) -> Viewport:
        """返回产品画布的实时尺寸。"""
        return self.sink.viewport

    def emit(self, view: ApplicationView) -> None:
        """保存输出事实，不修改文字、布局或样式。"""
        block = view.renderable
        if isinstance(block, StyledBlock):
            text = block.plain_text
        elif isinstance(block, FragmentBlock):
            text = "".join(value for _, value in block.fragments)
        else:
            text = ""
        self.views.append({"type": view.type, "text": text})
        self.sink.emit(view)


class ActiveTerminalFallback:
    """确保该场景始终由真实 TUI 拥有活动展示。"""

    async def stop(self) -> None:
        """拒绝在 TUI 尚未打开或已关闭后调用活动后备。"""
        raise RuntimeError("native MCP scenario lost its TUI activity owner")


async def await_cleanup(awaitable: Awaitable[None]) -> None:
    """按 Harness 清理屏障等待资源收束，取消不能越过释放。"""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def config_digest(path: Path) -> str:
    """计算场景配置字节摘要，用于验证控制命令没有写配置。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class McpScenario:
    """拥有一次验收的菜单宿主、MCP owner 和观测任务，退出时统一释放。"""

    def __init__(
        self, runtime: TuiRuntime, config: ConfigSession, facts: ScenarioFacts, *, animate: bool,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.facts = facts
        self.application = RecordedApplication(runtime)
        activity = FrontendActivity(runtime, ActiveTerminalFallback(), enabled=animate)
        self.external = ExternalMcpRuntime(McpRuntimeContext(
            config, activity.start_external_mcp, activity.stop, await_cleanup,
        ))
        self.owner = McpRuntimeOwner(runtime_factory=lambda: self.external)
        self.host = SimpleNamespace(
            frontend=SimpleNamespace(runtime=runtime, application=self.application),
            activity=activity,
            execution=SimpleNamespace(external_mcp=self.owner),
            lifecycle=SimpleNamespace(request_stop=self.request_stop),
        )
        self.foreground = TuiForegroundTasks(runtime, self.host)
        self.protocol_client = Mock()
        state = TuiSessionState(
            pref_config={}, model="fixture-model", workspace_label="mcp-acceptance",
            permissions=preset_permissions("full-access"),
        )
        self.dispatcher = TuiCommandDispatcher(
            self.host, runtime, state, self.foreground, protocol_client=self.protocol_client,
        )
        self.completed = 0
        self.probes: dict[str, ThawedJsonValue] = {}
        self.held_scope = contextlib.ExitStack()
        self.held_tools: ExternalToolGroupPort | None = None

    def request_stop(self) -> None:
        """记录 dispatcher 已按正式退出入口请求停止。"""
        self.facts.set_detail("stop_requested", True)

    def publish(self) -> None:
        """投影产品菜单、连接及输出事实，不根据展示文字推断运行状态。"""
        snapshot = self.owner.snapshot
        menu = self.runtime.screen.menu.state
        self.facts.set_detail("completed", self.completed)
        self.facts.set_detail("foreground", self.runtime.foreground_active)
        self.facts.set_detail("activity", self.runtime.activity.active)
        block = self.runtime.screen.activity_block
        self.facts.set_detail("activity_text", "" if block is None else "".join(text for _, text in block.fragments))
        self.facts.set_detail("width", self.runtime.terminal_width)
        self.facts.set_detail("height", self.runtime.terminal_height)
        self.facts.set_detail("render_counter", self.runtime.screen.application.render_counter)
        self.facts.set_detail("menu", {} if menu is None else {
            "view_id": menu.request.view_id,
            "title": menu.request.title,
            "selected": menu.selected,
            "scroll_top": menu.scroll_top,
            "labels": [option.label for option in menu.request.options],
        })
        self.facts.set_detail("services", [{
            "key": item.config_key, "state": item.state, "enabled": item.config_enabled,
            "tools": list(item.tools), "discovered": item.discovered, "filtered": item.filtered,
        } for item in snapshot.services])
        self.facts.set_detail("views", list(self.application.views))
        self.facts.set_detail("probes", dict(self.probes))
        self.facts.set_detail("held_tools", sorted(self.held_tools.tools) if self.held_tools is not None else [])
        self.facts.write()

    async def observe(self) -> None:
        """持续发布只读事实，父进程使用实际状态等待导航和资源收束。"""
        while True:
            self.publish()
            await asyncio.sleep(0.03)

    async def probe_ready_services(self) -> None:
        """通过正式工具使用范围执行实际 MCP ping，记录进程与会话身份。"""
        self.probes = {}
        for service in self.owner.snapshot.services:
            name = next((name for name in service.tools if name.endswith("__ping")), None)
            if name is None or service.state != "ready":
                continue
            with self.owner.use_tools() as tools:
                if tools is None:
                    raise RuntimeError("ready service has no tool scope")
                result = await tools.call_tool(name)
            if result.isError or not result.content or not isinstance(result.content[0], mcp_types.TextContent):
                raise RuntimeError("fixture ping did not return a successful text result")
            reply = FixtureReply.model_validate_json(result.content[0].text)
            self.probes[service.config_key] = {
                "instance_id": str(reply.instance_id), "pid": reply.pid,
                "session_id": reply.session_id, "call_count": reply.call_count,
            }

    async def read_commands(self) -> None:
        """从产品输入框读取命令，经正式 dispatcher、前台屏障和 owner 执行。"""
        while True:
            value = await self.runtime.read_message(PromptContext(
                model="fixture-model", workspace_label="mcp-acceptance",
                permissions_label="full access",
            ))
            submission = self.runtime.consume_submission_payload()
            if submission is None:
                raise RuntimeError("submitted command has no input payload")
            self.facts.record_submission(submission, queue_only=False)
            before = len(self.application.views)
            action = await self.dispatcher.dispatch(value)
            if self.protocol_client.mock_calls:
                raise RuntimeError("local MCP command contacted the protocol client")
            if action is DispatchAction.EXIT:
                break
            if action is not DispatchAction.HANDLED:
                raise RuntimeError("MCP command escaped to model execution")
            changed = self.application.views[before:]
            if any(view["type"] in {"tui.external_mcp.status", "tui.external_mcp.interrupted"} for view in changed):
                await self.probe_ready_services()
            self.facts.set_detail("latest_views", changed)
            self.completed += 1
            if self.facts.scenario == "busy" and self.completed == 1:
                self.held_tools = self.held_scope.enter_context(self.owner.use_tools("a"))
            elif self.facts.scenario == "busy" and self.completed == 3:
                self.held_scope.close()
                self.held_tools = None
            self.publish()

    async def run(self) -> None:
        """共同监督输入和观测任务，任一异常均结束场景并释放资源。"""
        observer = asyncio.create_task(self.observe())
        reader = asyncio.create_task(self.read_commands())
        self.facts.stage = "ready"
        try:
            done, _ = await asyncio.wait((observer, reader), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            observer.cancel()
            reader.cancel()
            await asyncio.gather(observer, reader, return_exceptions=True)
            self.held_scope.close()
            self.foreground.cancel()
            try:
                await self.foreground.wait()
            finally:
                await self.owner.close()
            self.facts.set_detail("owner_released", self.owner.current is None)
            self.facts.set_detail("pending_mcp_tasks", [
                task.get_name() for task in asyncio.all_tasks()
                if task.get_name().startswith("external MCP ") and not task.done()
            ])


async def execute(scenario: str, facts_path: Path, *, animate: bool) -> None:
    """在独立验收配置内启动真实 stdio/HTTP/SSE 服务，关闭后再退出原生终端。"""
    reset_sinks()
    facts = ScenarioFacts(facts_path, scenario)
    directory = facts_path.parent / "services"
    repository = Path.cwd()
    async with contextlib.AsyncExitStack() as stack:
        remotes = []
        if scenario == "control":
            specifications: tuple[tuple[str, FixtureTransport], ...] = (("H", "streamable_http"), ("S", "sse"))
            for name, transport in specifications:
                remotes.append(await stack.enter_async_context(remote_fixture(FixtureSpec(
                    directory, name, transport, repository=repository,
                ))))
        config_path = write_config(directory, tuple(remotes), repository=repository)
        store = ConfigStore(config_path)
        store.update({("mcp_servers", "Slow", "startup_timeout_sec"): 30.0})
        if scenario == "navigation":
            spec = FixtureSpec(directory, "long-name", repository=repository)
            store.update({("mcp_servers", "Long service name with spaces and a narrow terminal"): {
                "command": sys.executable, "args": list(spec.arguments()),
                "cwd": str(repository), "enabled": False,
            }})
        config = ConfigSession(store, workspace=directory)
        facts.set_detail("config_before", config_digest(config_path))
        facts.set_detail("remote_urls", {remote.spec.name: remote.url for remote in remotes})
        capabilities = detect_terminal_capabilities(input_stream=sys.stdin, output_stream=sys.stdout)
        runtime = TuiRuntime(input_obj=create_tui_input(sys.stdin), terminal_capabilities=capabilities)
        await runtime.open()
        try:
            driver = McpScenario(runtime, config, facts, animate=animate)
            await driver.run()
            facts.set_detail("config_after", config_digest(config_path))
            facts.set_detail("remote_processes_alive", all(remote.process.returncode is None for remote in remotes))
        finally:
            await runtime.close()
    facts.stage = "complete"
    facts.set_detail("remote_processes_released", all(remote.process.returncode is not None for remote in remotes))
    facts.write()
    print("PTY MCP CLOSED", flush=True)


def main() -> None:
    """解析原生 MCP 验收场景及动画策略。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("control", "busy", "failure", "navigation"))
    parser.add_argument("facts", type=Path)
    parser.add_argument("--no-animation", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(execute(arguments.scenario, arguments.facts, animate=not arguments.no_animation))


if __name__ == "__main__":
    main()
