"""通过原生按键、正式命令链和独立 MCP 服务验证控制行为。"""

import asyncio
import contextlib
import json
import os
import sys
import time
import typing

import httpx
import pytest

from pathlib import Path
from mcp import (
    ClientSession,
    types as mcp_types,
)
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from agent.protocol.json_value import (
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)
from infrastructure.mcp.transport import external_http_client
from tests.external_mcp.fixtures import (
    FixtureReply,
    read_facts,
)
from tests.pty import (
    PtyKey,
    TerminalEnvironment,
    TerminalHarness,
    TerminalInputSource,
    TerminalMode,
    TerminalSize,
    spawn_terminal,
)


pytestmark = pytest.mark.pty_acceptance
_INITIAL_SIZE = TerminalSize(rows=28, columns=100)


@pytest.mark.parametrize("transport, keys, decision, color, theme", (
    pytest.param("stdio", (b"\r",), "accept", True, "dark", id="allow-enter"),
    pytest.param("streamable_http", (b"\x1b[B", b"\r"), "acceptForSession", True, "dark", id="session-navigation"),
    pytest.param("stdio", (b"3",), "acceptAndRemember", True, "dark", id="persistent-number"),
    pytest.param("sse", (b"4",), "cancel", True, "dark", id="cancel-number"),
    pytest.param("stdio", (b"\x1b",), "cancel", True, "dark", id="cancel-escape"),
    pytest.param("streamable_http", (b"\x1b[B" * 3, b"\r"), "cancel", False, "dark", id="cancel-monochrome"),
    pytest.param("stdio", (b"\r",), "accept", True, "light", id="allow-light"),
))
def test_mcp_tool_approval_codex_card_and_real_call(
    tmp_path: Path, transport: str, keys: tuple[bytes, ...], decision: str, color: bool, theme: str,
) -> None:
    path = tmp_path / "facts.json"
    with spawn_terminal(
        [sys.executable, "-m", "tests.pty.mcp_approval_scenario", transport, str(path), "--theme", theme],
        cwd=Path.cwd(), env=os.environ, size=_INITIAL_SIZE,
        terminal=TerminalEnvironment(no_color=not color),
        failure_artifact_directory=tmp_path / "artifacts",
    ) as terminal:
        driver = McpTerminal(terminal, path)
        try:
            driver.wait(lambda details: "tty" in details)
            terminal.wait_for_screen_text("fixture-model")
            terminal.write_user_text("draft remains")
            driver.wait(lambda details: details.get("card_active") is True)
            terminal.wait_for_screen_text("Cancel this tool call")
            snapshot = terminal.screen.snapshot()
            screen = snapshot.visible_text
            assert 'Allow the approval MCP server to run tool "ping"?' in screen
            assert "Field 1/1" in screen
            assert "value: TTY 参数摘要" in screen and "..." in screen
            for label in ("1. Allow", "2. Allow for this session", "3. Always allow", "4. Cancel"):
                assert label in screen
            assert "enter to submit | esc to cancel" in screen
            assert "Risk:" not in screen and "(y)" not in screen
            prompt_row = next(i for i, line in enumerate(snapshot.visible_lines) if "Allow the approval" in line)
            selected_row = next(i for i, line in enumerate(snapshot.visible_lines) if "› 1." in line)
            footer_row = next(i for i, line in enumerate(snapshot.visible_lines) if "enter to submit" in line)
            prompt = terminal.screen.cell(prompt_row, 2)
            selected = terminal.screen.cell(selected_row, 2)
            footer = terminal.screen.cell(footer_row, 2)
            assert prompt.foreground == selected.foreground
            assert not prompt.bold
            assert prompt.background == footer.background
            assert (prompt.foreground != "default") is color
            assert (prompt.background != "default") is color
            terminal.save_failure_artifacts(tmp_path / "card")

            before_inputs = tuple(event for event in terminal.input_events if event.source is TerminalInputSource.USER)
            before_resize = driver.details()["render_counter"]
            assert isinstance(before_resize, int)
            driver.resize(TerminalSize(rows=18, columns=44))
            driver.wait(lambda details: isinstance(details["render_counter"], int) and details["render_counter"] > before_resize)
            terminal.wait_for_screen_text("enter to submit | esc to cancel")
            terminal.wait_for_screen_text("Always allow")
            assert "Cancel" in terminal.screen.snapshot().visible_text
            assert driver.details()["selected"] == 0
            assert driver.details()["draft"] == "draft remains"
            terminal.save_failure_artifacts(tmp_path / "narrow")
            driver.resize(_INITIAL_SIZE)
            terminal.wait_for_screen_text("Cancel this tool call")
            assert tuple(event for event in terminal.input_events if event.source is TerminalInputSource.USER) == before_inputs
            terminal.send_key(PtyKey.CTRL_A)
            driver.wait(lambda details: details["pager_active"] is True)
            terminal.wait_for_screen_text("Risk:")
            terminal.write_user_text("q")
            driver.wait(lambda details: details["pager_active"] is False)
            for key in keys:
                terminal.write_user(key)
            details = driver.wait(lambda details: "first" in details)
            first = _mapping(details["first"])
            assert first["decision"] == decision
            assert first["allowed"] is (decision != "cancel")
            facts = tuple(fact for file in (tmp_path / "services").glob("*.jsonl") for fact in read_facts(file))
            assert sum(fact.event == "tool.started" for fact in facts) == (0 if decision == "cancel" else 1)
            path.with_suffix(".repeat").write_text("continue", encoding="ascii")
            if decision in {"accept", "cancel"}:
                driver.wait(lambda details: details["card_call"] == "call-2")
                terminal.send_key(PtyKey.ESCAPE)
            details = driver.wait(lambda details: "second" in details)
            second = _mapping(details["second"])
            assert second["allowed"] is (decision in {"acceptForSession", "acceptAndRemember"})
            assert details["composer_submitted"] is False and details["draft"] == "draft remains"
            assert details["tty"] == {"stdin": True, "stdout": True}
            if decision == "acceptAndRemember":
                server = _mapping(_mapping(details["config"])["approval"])
                assert _mapping(_mapping(server["tools"])["ping"])["approval_mode"] == "approve"
            path.with_suffix(".ack").write_text("observed", encoding="ascii")
            assert terminal.wait_for_exit(timeout=15) == 0
            details = driver.details()
            assert details["owner_released"] is True and details["remote_released"] is True
            assert details["pending_mcp_tasks"] == []
            facts = tuple(fact for file in (tmp_path / "services").glob("*.jsonl") for fact in read_facts(file))
            expected_calls = 0 if decision == "cancel" else 1 if decision == "accept" else 2
            assert sum(fact.event == "tool.started" for fact in facts) == expected_calls
            assert sum(fact.event == "tool.completed" for fact in facts) == expected_calls
            _assert_stdio_sessions_closed(tmp_path / "services")
        finally:
            driver.save()


def _mapping(value: ThawedJsonValue) -> dict[str, ThawedJsonValue]:
    """收窄经过 JSON 边界校验的具名事实。"""
    assert isinstance(value, dict)
    return value


class McpTerminal:
    """把实际按键与已发布的产品事实对应，不直接修改产品菜单状态。"""

    def __init__(self, terminal: TerminalHarness, path: Path) -> None:
        self.terminal = terminal
        self.path = path
        self.size = _INITIAL_SIZE
        self.resizes: list[dict[str, int]] = []

    def details(self) -> dict[str, ThawedJsonValue]:
        """读取当前已完整发布的产品观测事实。"""
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        facts = thaw_object(freeze_json(loaded, field_name="MCP PTY facts"), field_name="MCP PTY facts")
        return _mapping(facts["details"])

    def wait(
        self, predicate: typing.Callable[[dict[str, ThawedJsonValue]], bool], *, timeout: float = 20.0,
    ) -> dict[str, ThawedJsonValue]:
        """等待实际状态收敛，超时保留终端输出作为诊断。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.path.exists():
                try:
                    details = self.details()
                except (OSError, json.JSONDecodeError):
                    details = None
                if details is not None and predicate(details):
                    return details
            if self.terminal.session.output_closed:
                break
            time.sleep(0.02)
        raise AssertionError(f"MCP PTY state did not converge\n{self.terminal.screen.snapshot().visible_text}")

    def ready(self) -> None:
        """等待产品完成初始化并开始读取输入。"""
        self.wait(lambda details: details.get("completed") == 0)
        self.terminal.wait_for_screen_text("fixture-model")

    def send(self, text: str) -> None:
        """通过输入框提交一条真实命令。"""
        self.terminal.write_user_text(text)
        self.terminal.send_key(PtyKey.ENTER)

    def completed(self) -> int:
        """返回已完成的本地命令数量。"""
        value = self.details()["completed"]
        assert isinstance(value, int)
        return value

    def command(self, text: str) -> dict[str, ThawedJsonValue]:
        """执行全量直接命令并等待最终结果与实际工具探测完成。"""
        count = self.completed()
        self.send(text)
        return self.wait(lambda details: details["completed"] == count + 1)

    def menu(self) -> dict[str, ThawedJsonValue]:
        """打开单服务菜单，不触发管理动作。"""
        self.send("/mcp")
        return self.wait(lambda details: _mapping(details["menu"]).get("view_id") == "mcp:root")

    def select(self, label: str) -> dict[str, ThawedJsonValue]:
        """用上下键移动到可见逻辑选项，不调用菜单内部选择函数。"""
        menu = _mapping(self.details()["menu"])
        labels = menu["labels"]
        selected = menu["selected"]
        assert isinstance(labels, list) and isinstance(selected, int)
        target = labels.index(label)
        difference = target - selected
        if difference:
            self.terminal.write_user((b"\x1b[B" if difference > 0 else b"\x1b[A") * abs(difference))
        return self.wait(lambda details: _mapping(details["menu"])["selected"] == target)

    def service(self, key: str) -> dict[str, ThawedJsonValue]:
        """从服务列表进入默认选择 status 的子菜单。"""
        self.select(key)
        self.terminal.send_key(PtyKey.ENTER)
        details = self.wait(lambda details: _mapping(details["menu"]).get("view_id") == "mcp:service")
        menu = _mapping(details["menu"])
        assert menu["labels"] == ["start", "force", "stop", "restart", "status"]
        assert menu["selected"] == 4
        return details

    def action(self, key: str, action: str) -> dict[str, ThawedJsonValue]:
        """经完整两级菜单执行单个服务动作。"""
        count = self.completed()
        self.menu()
        self.service(key)
        self.select(action)
        self.terminal.send_key(PtyKey.ENTER)
        details = self.wait(lambda details: details["completed"] == count + 1)
        assert not details["menu"] and not details["foreground"]
        return details

    def resize(self, size: TerminalSize) -> None:
        """记录实际缩放，并等待产品与 Screen 读到相同尺寸。"""
        width = self.details()["width"]
        assert isinstance(width, int)
        # 按已有输出 adapter 的可用宽度计算变化，保留它的右侧安全列。
        expected_width = width + size.columns - self.size.columns
        self.resizes.append({"rows": size.rows, "columns": size.columns,
                             "after_input": len(self.terminal.input_events)})
        self.terminal.resize(size)
        self.wait(lambda details: details["width"] == expected_width and details["height"] == size.rows)
        self.size = size

    def save(self) -> None:
        """在成功和失败时均保存按键、缩放、原始输出与最终画面。"""
        directory = self.path.parent / "artifacts"
        self.terminal.save_failure_artifacts(directory)
        directory.joinpath("inputs.json").write_text(json.dumps({
            "keys": [{"sequence": event.sequence, "source": event.source.value, "hex": event.data.hex()}
                     for event in self.terminal.input_events],
            "resizes": self.resizes,
        }, indent=2), encoding="utf-8")

    def close(self) -> None:
        """正常退出场景并核对资源所有权和配置字节。"""
        self.send("/quit")
        assert self.terminal.wait_for_exit(timeout=15.0) == 0
        self.terminal.session.wait_for_output("PTY MCP CLOSED")
        details = self.details()
        assert details["owner_released"] is True
        assert details["pending_mcp_tasks"] == []
        assert details["stop_requested"] is True
        assert details["config_before"] == details["config_after"]
        assert details["remote_processes_alive"] is True
        assert details["remote_processes_released"] is True
        modes = tuple(event.mode for event in self.terminal.mode_events)
        for enabled, disabled in (
            (TerminalMode.FOCUS_REPORTING_ENABLED, TerminalMode.FOCUS_REPORTING_DISABLED),
            (TerminalMode.BRACKETED_PASTE_ENABLED, TerminalMode.BRACKETED_PASTE_DISABLED),
            (TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED, TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED),
        ):
            assert modes.count(enabled) == modes.count(disabled)
        assert not self.terminal.screen.snapshot().cursor.hidden


async def _remote_ping(url: str, *, sse: bool) -> FixtureReply:
    """独立客户端重连已停止的远端，证明产品只断开自己的会话。"""
    async with asyncio.timeout(10.0), contextlib.AsyncExitStack() as stack:
        if sse:
            read, write = await stack.enter_async_context(sse_client(
                url, timeout=5.0, sse_read_timeout=5.0,
                httpx_client_factory=external_http_client,
            ))
        else:
            client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
            read, write, _ = await stack.enter_async_context(streamable_http_client(url, http_client=client))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.call_tool("ping")
        assert not result.isError and len(result.content) == 1
        item = result.content[0]
        assert isinstance(item, mcp_types.TextContent)
        return FixtureReply.model_validate_json(item.text)


def _assert_stdio_sessions_closed(directory: Path) -> None:
    """对照真实握手与关闭记录核对正常 stdio 服务释放。"""
    for path in directory.glob("*.jsonl"):
        facts = read_facts(path)
        opened = {(fact.instance_id, fact.session_id) for fact in facts
                  if fact.transport == "stdio" and fact.event == "session.opened"}
        closed = {(fact.instance_id, fact.session_id) for fact in facts
                  if fact.transport == "stdio" and fact.event == "session.closed"}
        assert opened == closed, path.name


def _spawn(scenario: str, path: Path, *, animate: bool = True, color: bool = True) -> TerminalHarness:
    """使用现有跨平台原生适配器，测试环境由父进程派生。"""
    return spawn_terminal(
        [sys.executable, "-m", "tests.pty.external_mcp_scenario", scenario, str(path),
         *([] if animate else ["--no-animation"])],
        cwd=Path.cwd(), env=os.environ, size=_INITIAL_SIZE,
        terminal=TerminalEnvironment(no_color=not color),
        failure_artifact_directory=path.parent / "artifacts",
    )


def _service(details: dict[str, ThawedJsonValue], key: str) -> dict[str, ThawedJsonValue]:
    """读取实际运行时投影的一个服务状态。"""
    services = details["services"]
    assert isinstance(services, list)
    return next(_mapping(item) for item in services if _mapping(item)["key"] == key)


def _identity(details: dict[str, ThawedJsonValue], key: str) -> tuple[ThawedJsonValue, ThawedJsonValue]:
    """取实际工具返回的进程与会话身份，避免用 PID 推断连接隔离。"""
    probe = _mapping(_mapping(details["probes"])[key])
    return probe["instance_id"], probe["session_id"]


def _result(details: dict[str, ThawedJsonValue]) -> str:
    """每次管理动作必须只提交一个最终结果块。"""
    views = details["latest_views"]
    assert isinstance(views, list)
    finals = [_mapping(view) for view in views if _mapping(view)["type"] in {
        "tui.external_mcp.status", "tui.external_mcp.interrupted",
    }]
    assert len(finals) == 1
    text = finals[0]["text"]
    assert isinstance(text, str)
    return " ".join(text.split())


@pytest.mark.parametrize(("animate", "color"), [(True, True), (False, False)])
def test_native_mcp_controls_real_transports_without_restarting_other_services(tmp_path: Path, animate: bool, color: bool):
    path = tmp_path / "facts.json"
    with _spawn("control", path, animate=animate, color=color) as terminal:
        client = McpTerminal(terminal, path)
        try:
            client.ready()
            initial = client.command("/mcp start")
            assert "all services" in _result(initial)
            a_before, b_before = _identity(initial, "A"), _identity(initial, "B")
            assert _service(initial, "E")["state"] == "ready"
            assert _service(initial, "E")["tools"] == []
            assert _service(initial, "Filtered")["filtered"] == 2
            assert _identity(initial, "H") and _identity(initial, "S")

            before_status = read_facts(tmp_path / "services" / "a.jsonl")
            client.menu()
            terminal.send_key(PtyKey.ENTER)
            terminal.send_key(PtyKey.ENTER)
            client.wait(lambda details: details["completed"] == 2)
            terminal.wait_for_screen_text("MCP Tools")
            assert read_facts(tmp_path / "services" / "a.jsonl") == before_status

            stopped = client.action("A", "stop")
            assert "A · stop complete" in _result(stopped)
            assert _service(stopped, "A")["state"] == "stopped"
            assert _identity(stopped, "B") == b_before
            started = client.command("/mcp start")
            assert _identity(started, "A") != a_before
            assert _identity(started, "B") == b_before
            disabled = client.action("D", "start")
            assert "disabled" in _result(disabled)
            assert _service(disabled, "D")["state"] == "stopped"
            assert not read_facts(tmp_path / "services" / "disabled.jsonl")
            forced = client.action("D", "force")
            assert "temporary connection" in _result(forced)
            assert _service(forced, "D")["enabled"] is False
            repeated = client.action("D", "force")
            assert _identity(repeated, "D") == _identity(forced, "D")
            restarted = client.action("D", "restart")
            assert _service(restarted, "D")["state"] == "stopped"
            assert _identity(restarted, "B") == b_before
            named_all = client.action("all", "force")
            assert _service(named_all, "all")["state"] == "ready"
            assert _identity(named_all, "B") == b_before
            assert _service(named_all, "D")["state"] == "stopped"
            stopped_all = client.command("/mcp stop")
            assert "all services" in _result(stopped_all)
            services = stopped_all["services"]
            assert isinstance(services, list)
            assert all(_mapping(service)["state"] == "stopped" for service in services)
            _assert_stdio_sessions_closed(tmp_path / "services")
            for key in ("H", "S"):
                remote_facts = read_facts(tmp_path / "services" / f"{key}.jsonl")
                assert sum(fact.event == "session.closed" for fact in remote_facts) == 1
                url = _mapping(stopped_all["remote_urls"])[key]
                assert isinstance(url, str)
                reply = asyncio.run(_remote_ping(url, sse=key == "S"))
                assert str(reply.instance_id) == _identity(initial, key)[0]
                assert reply.session_id != _identity(initial, key)[1]
            client.command("/mcp start")
            client.close()
            _assert_stdio_sessions_closed(tmp_path / "services")
        finally:
            client.save()


def test_native_mcp_exact_filters_block_calls_across_real_transports(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    with _spawn("filtering", path) as terminal:
        client = McpTerminal(terminal, path)
        try:
            client.ready()
            initial = client.command("/mcp start")
            assert initial["tty"] == {"stdin": True, "stdout": True}
            for key, prefix in (("A", "a"), ("B", "b"), ("H", "h"), ("S", "s")):
                service = _service(initial, key)
                assert service["state"] == "ready"
                assert service["tools"] == [f"mcp__{prefix}__ping"]
                assert service["discovered"] == 2 and service["filtered"] == 1
                assert _identity(initial, key)
            for key in ("Filtered", "Docs API", "Docs/API", "all"):
                service = _service(initial, key)
                assert service["state"] == "ready"
                assert service["tools"] == []
                assert service["discovered"] == service["filtered"] == 2
            rejected = initial["rejected_tools"]
            assert isinstance(rejected, list) and len(rejected) == 12
            client.command("/mcp status")
            terminal.wait_for_screen_text("MCP Tools")
            restarted = client.action("A", "restart")
            assert _identity(restarted, "A") != _identity(initial, "A")
            assert _identity(restarted, "B") == _identity(initial, "B")
            assert _service(restarted, "A")["tools"] == ["mcp__a__ping"]
            assert restarted["rejected_tools"] == rejected
            for name in ("a", "b", "H", "S"):
                calls = [fact.tool for fact in read_facts(tmp_path / "services" / f"{name}.jsonl")
                         if fact.event == "tool.started"]
                assert calls and set(calls) == {"ping"}
            for name in ("filtered", "alias-one", "alias-two", "named-all"):
                facts = read_facts(tmp_path / "services" / f"{name}.jsonl")
                assert any(fact.event == "tools.listed" for fact in facts)
                assert not any(fact.event == "tool.started" for fact in facts)
            client.close()
            _assert_stdio_sessions_closed(tmp_path / "services")
        finally:
            client.save()


def test_native_mcp_busy_scope_blocks_stop_and_keeps_its_tool_catalog(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    with _spawn("busy", path) as terminal:
        client = McpTerminal(terminal, path)
        try:
            client.ready()
            initial = client.command("/mcp start")
            assert initial["held_tools"] == ["mcp__a__block", "mcp__a__ping"]
            added = client.action("D", "force")
            assert _service(added, "D")["state"] == "ready"
            assert added["held_tools"] == initial["held_tools"]
            rejected = client.command("/mcp stop")
            assert "busy" in _result(rejected)
            for key in ("A", "B", "D"):
                assert _identity(rejected, key) == _identity(added, key)
            stopped = client.action("A", "stop")
            assert _service(stopped, "A")["state"] == "stopped"
            assert _identity(stopped, "B") == _identity(initial, "B")
            client.close()
            _assert_stdio_sessions_closed(tmp_path / "services")
        finally:
            client.save()


@pytest.mark.parametrize("animate", [True, False])
def test_native_mcp_failed_and_cancelled_start_leave_healthy_service_usable(tmp_path: Path, animate: bool) -> None:
    path = tmp_path / "facts.json"
    with _spawn("failure", path, animate=animate) as terminal:
        client = McpTerminal(terminal, path)
        try:
            client.ready()
            initial = client.action("B", "start")
            failed = client.action("F", "force")
            assert "failed" in _result(failed)
            assert _service(failed, "F")["state"] == "failed"
            assert _identity(failed, "B") == _identity(initial, "B")
            terminal.wait_for_screen_text("force failed")
            count = client.completed()
            client.menu()
            client.service("Slow")
            client.select("force")
            terminal.send_key(PtyKey.ENTER)
            client.wait(lambda details: details["foreground"] is True
                        and _service(details, "Slow")["state"] == "starting")
            starting = client.wait(lambda _: any(fact.event == "fault.injected"
                                                 for fact in read_facts(tmp_path / "services" / "slow.jsonl")))
            assert starting["activity"] is animate
            assert bool(starting["activity_text"]) is animate
            terminal.save_failure_artifacts(tmp_path / "starting")
            terminal.send_key(PtyKey.ESCAPE)
            cancelled = client.wait(lambda details: details["completed"] == count + 1)
            assert "interrupted" in _result(cancelled)
            assert _service(cancelled, "Slow")["state"] != "ready"
            assert _identity(cancelled, "B") == _identity(initial, "B")
            assert not cancelled["foreground"] and not cancelled["menu"]
            assert cancelled["activity"] is False
            terminal.wait_for_screen_text("force interrupted")
            invalid = client.command("/mcp stop B")
            assert invalid["services"] == cancelled["services"]
            assert invalid["probes"] == cancelled["probes"]
            client.close()
            _assert_stdio_sessions_closed(tmp_path / "services")
        finally:
            client.save()


def test_native_mcp_long_menu_resize_and_escape_preserve_target_without_control(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    with _spawn("navigation", path) as terminal:
        client = McpTerminal(terminal, path)
        try:
            client.ready()
            root = client.menu()
            labels = _mapping(root["menu"])["labels"]
            assert isinstance(labels, list) and "All servers" not in labels
            key = "Long service name with spaces and a narrow terminal"
            selected = client.select(key)
            index = _mapping(selected["menu"])["selected"]
            for columns in (120, 80, 40):
                client.resize(TerminalSize(rows=15, columns=columns))
                details = client.wait(lambda details: _mapping(details["menu"])["selected"] == index
                                      and _mapping(details["menu"])["scroll_top"] > 0)
                terminal.wait_for_screen_text("Long service name")
                terminal.wait_for_screen_text("Press enter")
                terminal.save_failure_artifacts(tmp_path / f"width-{columns}")
            scroll = _mapping(details["menu"])["scroll_top"]
            client.service(key)
            terminal.wait_for_screen_text("status")
            terminal.send_key(PtyKey.ESCAPE)
            returned = client.wait(lambda details: _mapping(details["menu"]).get("view_id") == "mcp:root")
            assert _mapping(returned["menu"])["selected"] == index
            assert _mapping(returned["menu"])["scroll_top"] == scroll
            terminal.send_key(PtyKey.ESCAPE)
            cancelled = client.wait(lambda details: details["completed"] == 1)
            assert not cancelled["menu"] and cancelled["probes"] == {}
            assert not list((tmp_path / "services").glob("*.jsonl"))
            client.resize(TerminalSize(rows=28, columns=100))
            client.close()
        finally:
            client.save()
