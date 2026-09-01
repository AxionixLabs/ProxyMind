# -*- coding: utf-8 -*-

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from metadata import const

from frontends.cli import (
    bootstrap,
    entry,
)
from frontends.cli import dispatch as cli_dispatch
from frontends.cli import frontend as cli_frontend
from frontends.cli.commands import (
    AgentListenCommand,
    CliCommand,
    CliInvocation,
    CompletionCommand,
    DoctorCommand,
    ExecCommand,
    InteractiveCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpSetEnabledCommand,
    McpServerCommand,
    ResumeCommand,
    RuntimeUpgradeCommand,
    SessionArchiveCommand,
)
from frontends.cli.frontend import (
    resolve_cli_design,
    resolve_cli_frontend,
)
from frontends.cli.help import (
    ANSI_ACCENT,
    ANSI_HEADER,
    ANSI_MUTED
)
from frontends.cli.arguments import create_cli_parser
from frontends.cli.parser import (
    parse_cli_command,
    parse_cli_invocation
)
from agent.harness.hooks.registry import HookRegistry


def _runtime_services() -> SimpleNamespace:
    """构造 bootstrap 单测使用的显式 Hook 组合工厂。"""
    return SimpleNamespace(
        environment_capability=SimpleNamespace(clear_cache=Mock()),
        create_hook_registry=lambda **kwargs: HookRegistry(**kwargs),
    )
from frontends.cli.selection import OutputMode, resolve_cli_output_mode
from frontends.cli.dispatch import run_selected_command
from agent.application.turns.run_result import RunResult
from agent.application import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner
from frontends.runtime import PassiveFrontendRuntime
from frontends.output.application import ConsoleApplicationSink
from frontends.output.application import JsonApplicationSink
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.schema import ConfigOverride
from frontends.terminal.capabilities import DEGRADED_TERMINAL_CAPABILITIES
from agent.domain.policies import preset_permissions
from infrastructure.errors import AppError


@pytest.fixture
def root_turn_adapter(monkeypatch) -> AsyncMock:
    """替换 CLI 直接依赖的根轮次用例。"""
    turn_runner = AsyncMock()
    monkeypatch.setattr(cli_dispatch, "run_root_turn", turn_runner)
    return turn_runner


@pytest.fixture(autouse=True)
def injected_turn_application(monkeypatch) -> None:
    """为无完整 Controller 的 CLI 单测注入 Session runtime。"""
    monkeypatch.setattr(
        cli_dispatch,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


@pytest.fixture(autouse=True)
def frozen_environment_snapshot(monkeypatch) -> dict[str, object]:
    """固定 CLI 提交边界捕获的环境事实。"""
    snapshot = {"snapshot_id": "envsnap_cli"}
    monkeypatch.setattr(
        cli_dispatch,
        "capture_active_turn_environment",
        Mock(return_value=snapshot),
    )
    return snapshot


async def _await_cleanup(awaitable) -> None:
    await awaitable


def test_gravity_option_is_removed() -> None:
    parser = create_cli_parser()

    assert "--gravity" not in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["--gravity", "archive"])


def test_attach_option_is_removed() -> None:
    parser = create_cli_parser()

    assert "--attach" not in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["exec", "hello", "--attach", "report.pdf"])


@pytest.mark.parametrize("option", ("--chat", "--fast", "--xtra", "--agent"))
def test_legacy_entry_options_are_removed(option: str) -> None:
    parser = create_cli_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([option])


def test_cli_parser_returns_typed_commands() -> None:
    assert parse_cli_command([]) == InteractiveCommand()
    assert parse_cli_command(["exec", "hello"]) == ExecCommand(prompt="hello")
    assert parse_cli_command(["resume"]) == ResumeCommand()
    assert parse_cli_command(["archive", "sid_archive_1_abcdef"]) == SessionArchiveCommand(
        action="archive",
        target="sid_archive_1_abcdef",
    )
    assert parse_cli_command(["unarchive", "A saved session"]) == SessionArchiveCommand(
        action="unarchive",
        target="A saved session",
    )
    assert parse_cli_command(["completion"]) == CompletionCommand()
    assert parse_cli_command([
        "completion",
        "powershell",
    ]) == CompletionCommand(shell="powershell")
    assert parse_cli_command([
        "resume",
        "--last",
        "continue with the review",
        "--all",
        "--include-non-interactive",
        "--model",
        "review-model",
    ]) == ResumeCommand(
        prompt="continue with the review",
        model="review-model",
        last=True,
        all_workspaces=True,
        include_non_interactive=True,
    )
    assert parse_cli_command([
        "exec",
        "hello",
        "--json",
        "--dangerously-bypass-hook-trust",
        "--helix",
        "--image",
        "screen.png",
        "--model",
        "exec-model",
    ]) == ExecCommand(
        prompt="hello",
        images=("screen.png",),
        model="exec-model",
        output_format="json",
        helix_profile="app",
        bypass_hook_trust=True,
    )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (["--helix"], InteractiveCommand(helix_profile="app")),
        (["-H"], InteractiveCommand(helix_profile="app")),
        (["--helix", "api"], InteractiveCommand(helix_profile="api")),
        (["-H", "api"], InteractiveCommand(helix_profile="api")),
        (
            ["--helix", "initial task"],
            InteractiveCommand(prompt="initial task", helix_profile="app"),
        ),
        (
            ["-H", "initial task"],
            InteractiveCommand(prompt="initial task", helix_profile="app"),
        ),
        (
            ["exec", "inspect", "--helix"],
            ExecCommand(prompt="inspect", helix_profile="app"),
        ),
        (
            ["exec", "inspect", "--helix", "app"],
            ExecCommand(prompt="inspect", helix_profile="app"),
        ),
        (
            ["exec", "inspect", "--helix", "api"],
            ExecCommand(prompt="inspect", helix_profile="api"),
        ),
        (
            ["exec", "--helix", "inspect"],
            ExecCommand(prompt="inspect", helix_profile="app"),
        ),
        (
            ["exec", "--helix", "api", "inspect"],
            ExecCommand(prompt="inspect", helix_profile="api"),
        ),
        (
            ["resume", "--helix=api"],
            ResumeCommand(helix_profile="api"),
        ),
        (
            ["agent", "listen", "--helix", "api"],
            AgentListenCommand(helix_profile="api"),
        ),
    ),
)
def test_helix_profile_is_parsed_across_runtime_commands(
    arguments,
    expected,
) -> None:
    assert parse_cli_command(arguments) == expected


def test_helix_profile_rejects_unknown_explicit_value() -> None:
    with pytest.raises(SystemExit):
        parse_cli_command(["exec", "inspect", "--helix=other"])
    with pytest.raises(SystemExit):
        parse_cli_command(["exec", "inspect", "-H=other"])


def test_permission_options_are_process_level_config_overrides() -> None:
    invocation = parse_cli_invocation([
        "exec",
        "hello",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "on-request",
    ])

    assert invocation.command == ExecCommand(prompt="hello")
    assert invocation.config_overrides == (
        ConfigOverride(("sandbox_mode",), "workspace-write"),
        ConfigOverride(("approval_policy",), "on-request"),
    )


def test_legacy_access_option_is_removed() -> None:
    with pytest.raises(SystemExit):
        parse_cli_command(["exec", "hello", "--access", "full"])
    assert parse_cli_command(["agent", "listen"]) == AgentListenCommand()
    assert parse_cli_command(["upgrade", "helix"]) == RuntimeUpgradeCommand()
    assert parse_cli_command(["doctor"]) == DoctorCommand()
    assert parse_cli_command(["doctor", "--json"]) == DoctorCommand(
        output_format="json"
    )
    assert parse_cli_command(["mcp-server"]) == McpServerCommand()


def test_old_helix_upgrade_path_is_removed() -> None:
    with pytest.raises(SystemExit):
        parse_cli_command(["helix", "upgrade"])


def test_batch_command_is_removed() -> None:
    with pytest.raises(SystemExit):
        parse_cli_command(["batch", "cases.md", "--mode", "chat"])


def test_shared_runtime_options_merge_across_exec_command_boundary() -> None:
    assert parse_cli_command([
        "--image",
        "root.png",
        "--model",
        "root-model",
        "exec",
        "inspect",
        "--image",
        "first.png,second.png",
        "--model",
        "exec-model",
    ]) == ExecCommand(
        prompt="inspect",
        images=("root.png", "first.png", "second.png"),
        model="exec-model",
    )


def test_exec_alias_and_image_before_prompt_are_supported() -> None:
    assert parse_cli_command([
        "e",
        "--image",
        "screen.png",
        "inspect",
    ]) == ExecCommand(
        prompt="inspect",
        images=("screen.png",),
    )


def test_shared_runtime_options_propagate_to_resume() -> None:
    assert parse_cli_command([
        "--image",
        "screen.png",
        "--model",
        "review-model",
        "resume",
        "--last",
        "continue",
    ]) == ResumeCommand(
        prompt="continue",
        images=("screen.png",),
        model="review-model",
        last=True,
    )


def test_cli_help_uses_unified_plain_layout(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    help_text = create_cli_parser().format_help()

    assert help_text.startswith(
        f"{const.APP_DESC} CLI\n\n"
        "If no subcommand is specified, options will be forwarded to the "
        "interactive CLI.\n\n"
        "Usage: mind [OPTIONS] [PROMPT]\n"
        "       mind [OPTIONS] <COMMAND> [ARGS]\n"
    )
    assert "\nCommands:\n  exec" in help_text
    assert "\n  help            Print this message or the help" in help_text
    assert "\n  upgrade         Manage runtime component upgrades" in help_text
    assert "\n    listen" not in help_text
    assert "\n    helix" not in help_text
    assert "\nOptions:\n  -c, --config <key=value>" in help_text
    assert "\n          Print version\n" in help_text
    assert "-V, --version" in help_text
    assert "positional arguments" not in help_text
    assert "optional arguments" not in help_text
    assert "\x1b[" not in help_text


def test_cli_help_separates_argument_and_option_blocks(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        create_cli_parser().parse_args(["exec", "--help"])

    assert exit_info.value.code == 0
    exec_help = capsys.readouterr().out
    assert (
        "  [PROMPT]\n"
        "          Task instructions; use '-' or a pipe to read from standard input\n\n"
        "Options:"
    ) in exec_help
    assert "\n  --mode " not in exec_help
    root_help = create_cli_parser().format_help()
    normalized_root_help = " ".join(root_help.split())
    assert (
        "-s, --sandbox <SANDBOX_MODE> Select the sandbox policy to use when "
        "executing model-generated shell commands [possible values: "
        "read-only, workspace-write, danger-full-access]"
    ) in normalized_root_help
    assert (
        "-a, --ask-for-approval <APPROVAL_POLICY> Configure when the model "
        "requires human approval before executing a command Possible values: "
        "- untrusted: Only run \"trusted\" commands (e.g. ls, cat, sed) "
        "without asking for user approval. Will escalate to the user if the "
        "model proposes a command that is not in the \"trusted\" set "
        "- on-request: The model decides when to ask the user for approval "
        "- never: Never ask for user approval. Execution failures are "
        "immediately returned to the model"
    ) in normalized_root_help
    assert "Possible values:\n          - untrusted:" in root_help
    assert "-H, --helix [<PROFILE>]" in root_help
    assert "When PROFILE is omitted, app is used" in root_help


def test_cli_help_uses_accent_and_muted_terminal_colors(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    help_text = create_cli_parser().format_help()

    assert f"{ANSI_ACCENT}{const.APP_DESC} CLI" in help_text
    assert f"{ANSI_HEADER}Commands:" in help_text
    assert f"{ANSI_MUTED}If no subcommand is specified" in help_text
    assert f"{ANSI_MUTED}Run a task non-interactively" in help_text


def test_no_color_overrides_forced_help_color(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    assert "\x1b[" not in create_cli_parser().format_help()


@pytest.mark.parametrize("flag", ("-h", "--help"))
def test_root_help_flags_share_output(monkeypatch, capsys, flag: str) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        create_cli_parser().parse_args([flag])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith(
        f"{const.APP_DESC} CLI\n\n"
    )


def test_root_help_does_not_expose_removed_flow_command(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        parse_cli_command(["--help"])

    assert exit_info.value.code == 0
    assert "flow" not in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    ("arguments", "usage"),
    (
        (("exec", "--help"), "Usage: mind exec [OPTIONS] [PROMPT]"),
        (
            ("resume", "--help"),
            "Usage: mind resume [OPTIONS] [SESSION_ID] [PROMPT]",
        ),
        (("agent", "--help"), "Usage: mind agent <COMMAND> [ARGS]"),
        (("agent", "listen", "--help"), "Usage: mind agent listen [OPTIONS]"),
        (("upgrade", "--help"), "Usage: mind upgrade <COMPONENT> [ARGS]"),
        (("upgrade", "helix", "--help"), "Usage: mind upgrade helix [OPTIONS]"),
        (("doctor", "--help"), "Usage: mind doctor [OPTIONS]"),
        (
            ("completion", "--help"),
            "Usage: mind completion [OPTIONS] [SHELL]",
        ),
        (("mcp-server", "--help"), "Usage: mind mcp-server [OPTIONS]"),
        (("help", "--help"), "Usage: mind help [COMMAND]..."),
    ),
)
def test_subcommand_help_uses_stable_usage(
    monkeypatch,
    capsys,
    arguments: tuple[str, ...],
    usage: str,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        create_cli_parser().parse_args(arguments)

    assert exit_info.value.code == 0
    assert usage in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arguments", "usage"),
    (
        (("help",), "Usage: mind [OPTIONS]"),
        (("help", "exec"), "Usage: mind exec [OPTIONS] [PROMPT]"),
        (("help", "agent"), "Usage: mind agent <COMMAND> [ARGS]"),
        (
            ("help", "agent", "listen"),
            "Usage: mind agent listen [OPTIONS]",
        ),
        (
            ("help", "upgrade", "helix"),
            "Usage: mind upgrade helix [OPTIONS]",
        ),
    ),
)
def test_help_command_resolves_command_paths(
    monkeypatch,
    capsys,
    arguments: tuple[str, ...],
    usage: str,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        parse_cli_command(arguments)

    assert exit_info.value.code == 0
    assert usage in capsys.readouterr().out


def test_help_command_rejects_unknown_path(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    with pytest.raises(SystemExit) as exit_info:
        parse_cli_command(["help", "missing"])

    assert exit_info.value.code == 2
    assert "unknown help topic: missing" in capsys.readouterr().err


def test_process_entry_parses_command_once(monkeypatch, tmp_path) -> None:
    command = ExecCommand(prompt="inspect")
    invocation = CliInvocation(command=command)
    parse = Mock(return_value=invocation)
    route = AsyncMock(return_value=0)

    monkeypatch.setattr(entry, "parse_cli_invocation", parse)
    monkeypatch.setattr(entry, "main", route)

    result = entry.run(
        entry_file=str(tmp_path / "mind.py"),
        arguments=["exec", "inspect"],
    )

    assert result == 0
    parse.assert_called_once_with(["exec", "inspect"])
    route.assert_awaited_once_with(
        command,
        entry_file=str(tmp_path / "mind.py"),
        config_overrides=(),
        config_profile=None,
        runtime_services=None,
    )


def test_exec_reads_prompt_from_standard_input() -> None:
    command = parse_cli_command(
        ["exec", "-"],
        input_stream=StringIO("inspect the workspace\n"),
    )

    assert command == ExecCommand(prompt="inspect the workspace")


def test_exec_appends_piped_stdin_to_prompt_argument() -> None:
    command = parse_cli_command(
        ["exec", "summarize this"],
        input_stream=StringIO("command output\n"),
    )

    assert command == ExecCommand(
        prompt=(
            "summarize this\n\n"
            "<stdin>\n"
            "command output\n"
            "</stdin>"
        ),
    )


@pytest.mark.anyio
async def test_direct_cli_command_forwards_images_to_initial_request(
    root_turn_adapter,
) -> None:
    run_result = RunResult(status="completed", assistant_text="done")
    attachments = [{"kind": "image", "data_url": "data:image/png;base64,AA=="}]
    attach = SimpleNamespace(
        add_pending_attachments=Mock(),
        consume_pending_attachments=Mock(return_value=attachments),
    )
    root_turn_adapter.return_value = run_result
    mind = SimpleNamespace(
        attach=attach,
        exit_code=99,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )
    command = ExecCommand(
        prompt="hello",
        images=("screen.png",),
    )

    result = await run_selected_command(mind, command)

    assert result is run_result
    assert mind.exit_code == 0
    attach.add_pending_attachments.assert_called_once_with("screen.png")
    attach.consume_pending_attachments.assert_called_once_with()
    root_turn_adapter.assert_awaited_once_with(
        mind,
        message="hello",
        attachments=attachments,
        exec_env={"snapshot_id": "envsnap_cli"},
    )


@pytest.mark.anyio
async def test_direct_cli_command_applies_temporary_model_override(
    root_turn_adapter,
) -> None:
    run_result = RunResult(status="completed", assistant_text="done")
    fresh_pref_config = AsyncMock(return_value={
        "primary": {
            "model": "configured-model",
            "enabled": True,
        },
    })
    root_turn_adapter.return_value = run_result
    mind = SimpleNamespace(
        fresh_pref_config=fresh_pref_config,
        exit_code=99,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    result = await run_selected_command(
        mind,
        ExecCommand(prompt="hello", model="exec-model"),
    )

    assert result is run_result
    fresh_pref_config.assert_awaited_once_with(ttl_sec=0.0)
    root_turn_adapter.assert_awaited_once_with(
        mind,
        pref_config={
            "primary": {
                "model": "exec-model",
                "enabled": True,
            },
        },
        message="hello",
        attachments=[],
        exec_env={"snapshot_id": "envsnap_cli"},
    )


@pytest.mark.anyio
async def test_resume_last_uses_existing_tui_session_loop(monkeypatch) -> None:
    from frontends.tui.core import runtime as runtime_module
    from frontends.tui.features import history as history_module

    record = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    metadata = dict(record)
    events = []
    replay_blocks = (object(),)
    runtime = SimpleNamespace(
        terminal_width=80,
        hyperlinks_enabled=True,
        terminal_capabilities=DEGRADED_TERMINAL_CAPABILITIES,
        replace_transcript=Mock(
            side_effect=lambda _blocks: events.append("replace")
        ),
    )
    load_history_transcript = Mock(
        side_effect=lambda *_args, **_kwargs: (
            events.append("load") or replay_blocks
        ),
    )
    resume_conversation = AsyncMock(
        side_effect=lambda *_args, **_kwargs: (
            events.append("resume") or metadata
        ),
    )
    run_tui_loop = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("run")
    )
    attachments = Mock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=object()),
        history_workspace=r"D:\workspace",
        recent_conversation_sessions=Mock(return_value=[record]),
        resume_conversation=resume_conversation,
        attach=SimpleNamespace(add_pending_attachments=attachments),
        subscription=SimpleNamespace(close=AsyncMock()),
        task_event=asyncio.Event(),
        permissions=preset_permissions("auto"),
    )
    monkeypatch.setattr(
        runtime_module,
        "require_tui_runtime",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        history_module,
        "load_history_transcript",
        load_history_transcript,
    )
    monkeypatch.setattr(
        "frontends.tui.session.loop.run_tui_loop",
        run_tui_loop,
    )

    result = await run_selected_command(
        mind,
        ResumeCommand(
            prompt="continue",
            images=("screen.png",),
            model="review-model",
            last=True,
        ),
    )

    assert result is None
    mind.recent_conversation_sessions.assert_called_once_with(
        workspace=r"D:\workspace",
        sources=("tui", "tui:resume"),
        limit=1,
        status="active",
    )
    load_history_transcript.assert_called_once_with(
        mind,
        record["sid"],
        terminal_width=80,
        hyperlinks=True,
        terminal_capabilities=DEGRADED_TERMINAL_CAPABILITIES,
        record=record,
    )
    mind.resume_conversation.assert_called_once_with(
        record,
        source="tui:resume",
    )
    runtime.replace_transcript.assert_called_once_with(replay_blocks)
    attachments.assert_called_once_with("screen.png")
    run_tui_loop.assert_awaited_once()
    args, kwargs = run_tui_loop.await_args
    assert args == (mind,)
    assert callable(kwargs.pop("turn_runner"))
    assert kwargs == {
        "initial_prompt": "continue",
        "initial_images": ("screen.png",),
        "initial_model": "review-model",
    }
    mind.subscription.close.assert_awaited_once_with()
    assert events == ["load", "resume", "replace", "run"]


@pytest.mark.anyio
async def test_failed_cli_resume_does_not_replace_transcript(monkeypatch) -> None:
    from frontends.tui.core import runtime as runtime_module
    from frontends.tui.features import history as history_module

    record = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    runtime = SimpleNamespace(
        terminal_width=80,
        hyperlinks_enabled=False,
        terminal_capabilities=DEGRADED_TERMINAL_CAPABILITIES,
        replace_transcript=Mock(),
    )
    run_tui_loop = AsyncMock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=object()),
        history_workspace=r"D:\workspace",
        recent_conversation_sessions=Mock(return_value=[record]),
        resume_conversation=AsyncMock(return_value=None),
        task_event=asyncio.Event(),
        permissions=preset_permissions("auto"),
    )
    monkeypatch.setattr(
        runtime_module,
        "require_tui_runtime",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        history_module,
        "load_history_transcript",
        Mock(return_value=(object(),)),
    )
    monkeypatch.setattr(
        "frontends.tui.session.loop.run_tui_loop",
        run_tui_loop,
    )

    with pytest.raises(AppError, match="Session could not be resumed"):
        await run_selected_command(
            mind,
            ResumeCommand(last=True),
        )

    runtime.replace_transcript.assert_not_called()
    run_tui_loop.assert_not_awaited()


@pytest.mark.anyio
async def test_interactive_cli_resume_opens_picker_for_empty_snapshot(
    monkeypatch,
) -> None:
    from frontends.cli import dispatch as dispatch_module
    from frontends.tui.core import runtime as runtime_module
    from frontends.tui.features import history as history_module

    runtime = SimpleNamespace(
        terminal_capabilities=DEGRADED_TERMINAL_CAPABILITIES,
    )
    choose = AsyncMock(return_value=None)
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=object()),
        history_workspace=r"D:\workspace",
        recent_conversation_sessions=Mock(return_value=[]),
    )
    monkeypatch.setattr(
        runtime_module,
        "require_tui_runtime",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(history_module, "choose_history_session", choose)

    selected = await dispatch_module._select_resume_session(
        mind,
        ResumeCommand(),
    )

    assert selected is None
    mind.recent_conversation_sessions.assert_called_once_with(
        workspace=r"D:\workspace",
        sources=("tui", "tui:resume"),
        limit=200,
    )
    call = choose.await_args
    assert call.args == (runtime, [])
    assert call.kwargs["filter_workspace"] == r"D:\workspace"
    assert not call.kwargs["show_workspace"]
    assert isinstance(
        call.kwargs["preview_loader"],
        history_module.HistoryResumePreviewLoader,
    )


@pytest.mark.anyio
async def test_resume_last_empty_snapshot_keeps_direct_error() -> None:
    from frontends.cli import dispatch as dispatch_module

    mind = SimpleNamespace(
        history_workspace="D:/workspace",
        recent_conversation_sessions=Mock(return_value=[]),
    )

    with pytest.raises(AppError, match="No resumable sessions were found"):
        await dispatch_module._select_resume_session(
            mind,
            ResumeCommand(last=True),
        )


@pytest.mark.anyio
async def test_agent_listen_owns_listener_for_tui_session(monkeypatch) -> None:
    listener = object()
    run_tui_loop = AsyncMock()
    subscription = SimpleNamespace(
        start=Mock(return_value=listener),
        close=AsyncMock(),
    )
    mind = SimpleNamespace(
        attach=SimpleNamespace(add_pending_attachments=Mock()),
        permissions=preset_permissions("auto"),
        subscription=subscription,
    )
    monkeypatch.setattr(
        "frontends.tui.session.loop.run_tui_loop",
        run_tui_loop,
    )

    result = await run_selected_command(mind, AgentListenCommand())

    assert result is None
    subscription.start.assert_called_once_with()
    run_tui_loop.assert_awaited_once()
    args, kwargs = run_tui_loop.await_args
    assert args == (mind,)
    assert callable(kwargs.pop("turn_runner"))
    assert kwargs == {
        "initial_prompt": None,
        "initial_images": (),
        "initial_model": None,
    }
    subscription.close.assert_awaited_once_with()


@pytest.mark.anyio
async def test_agent_listen_stops_listener_when_tui_fails(monkeypatch) -> None:
    subscription = SimpleNamespace(
        start=Mock(),
        close=AsyncMock(),
    )
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        subscription=subscription,
    )
    monkeypatch.setattr(
        "frontends.tui.session.loop.run_tui_loop",
        AsyncMock(side_effect=RuntimeError("TUI failed")),
    )

    with pytest.raises(RuntimeError, match="TUI failed"):
        await run_selected_command(mind, AgentListenCommand())

    subscription.close.assert_awaited_once_with()


@pytest.mark.anyio
async def test_failed_exec_sets_nonzero_exit_code(root_turn_adapter) -> None:
    run_result = RunResult(status="failed", error="request failed")
    root_turn_adapter.return_value = run_result
    mind = SimpleNamespace(
        exit_code=0,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    result = await run_selected_command(mind, ExecCommand(prompt="hello"))

    assert result is run_result
    assert mind.exit_code == 1


@pytest.mark.anyio
async def test_exec_exit_code_comes_from_agent_event_projection(
    monkeypatch,
) -> None:
    run_result = RunResult(status="completed", assistant_text="done")
    submit = AsyncMock(return_value=SimpleNamespace(
        value=run_result,
        projection=SimpleNamespace(exit_code=7, status="projected"),
    ))
    close = AsyncMock()
    observe = Mock()
    monkeypatch.setattr(cli_dispatch, "observe", observe)
    monkeypatch.setattr(
        cli_dispatch,
        "TurnApplication",
        Mock(return_value=SimpleNamespace(submit=submit, close=close)),
    )
    mind = SimpleNamespace(
        exit_code=0,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    result = await run_selected_command(mind, ExecCommand(prompt="hello"))

    assert result is run_result
    assert mind.exit_code == 7
    submit.assert_awaited_once()
    close.assert_awaited_once_with(cancel_running=True)
    assert observe.call_args_list[-1].kwargs["outcome"] == "projected"


@pytest.mark.anyio
async def test_exec_uses_durable_runtime_composition_for_real_layout(
    monkeypatch,
    tmp_path,
) -> None:
    run_result = RunResult(status="completed", assistant_text="done")
    submit = AsyncMock(return_value=SimpleNamespace(
        value=run_result,
        projection=SimpleNamespace(exit_code=0, status="completed"),
    ))
    close = AsyncMock()
    application = SimpleNamespace(submit=submit, close=close)
    open_application = Mock(return_value=application)
    derive_session = Mock(return_value="cli_session_stable")
    db_path = tmp_path / "runtime.db"
    coordinates = {"cid": "cid-online", "sid": "sid-online"}
    monkeypatch.setattr(cli_dispatch, "agent_runtime_db_path", lambda: db_path)
    monkeypatch.setattr(cli_dispatch, "derive_local_session_id", derive_session)
    mind = SimpleNamespace(
        application_layout=object(),
        conversation=SimpleNamespace(snapshot=Mock(return_value=coordinates)),
        exit_code=0,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    result = await run_selected_command(
        mind,
        ExecCommand(prompt="hello"),
        turn_application_factory=open_application,
    )

    submitted_command = submit.await_args.args[0]
    assert result is run_result
    assert submitted_command.session_id == "cli_session_stable"
    assert submitted_command.environment_snapshot_value() == {
        "snapshot_id": "envsnap_cli",
    }
    open_application.assert_called_once_with(db_path)
    derive_session.assert_called_once_with("cli", coordinates)
    close.assert_awaited_once_with(cancel_running=True)


@pytest.mark.anyio
async def test_exec_requires_explicit_turn_application_factory_for_real_layout() -> None:
    mind = SimpleNamespace(
        application_layout=object(),
        exit_code=0,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    with pytest.raises(
        RuntimeError,
        match="CLI turn application factory is required",
    ):
        await run_selected_command(mind, ExecCommand(prompt="hello"))


@pytest.mark.anyio
async def test_cancelled_exec_closes_agent_session_worker(
    root_turn_adapter,
) -> None:
    started = asyncio.Event()

    async def wait_for_cancellation(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    root_turn_adapter.side_effect = wait_for_cancellation
    mind = SimpleNamespace(
        exit_code=0,
        history_workspace=".",
        permissions=preset_permissions("auto"),
    )

    task = asyncio.create_task(
        run_selected_command(mind, ExecCommand(prompt="wait"))
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not [
        item
        for item in asyncio.all_tasks()
        if item is not asyncio.current_task()
        and item.get_name().startswith("agent session ")
        and not item.done()
    ]


def test_upgrade_uses_text_frontend_without_tui_runtime() -> None:
    command = RuntimeUpgradeCommand()

    output_mode = resolve_cli_output_mode(command)
    frontend = resolve_cli_frontend(output_mode)
    design = resolve_cli_design(frontend, output_mode)

    assert output_mode == "text"
    assert isinstance(frontend.application, ConsoleApplicationSink)
    assert isinstance(frontend.runtime, PassiveFrontendRuntime)
    assert design is not None


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        (InteractiveCommand(), "tui"),
        (ResumeCommand(), "tui"),
        (ExecCommand(prompt="inspect"), "text"),
        (ExecCommand(prompt="inspect", output_format="json"), "json"),
        (AgentListenCommand(), "tui"),
        (RuntimeUpgradeCommand(), "text"),
        (SessionArchiveCommand(action="archive", target="session"), "text"),
        (DoctorCommand(), "text"),
        (DoctorCommand(output_format="json"), "json"),
        (McpListCommand(), "text"),
        (McpListCommand(output_format="json"), "json"),
        (McpGetCommand(name="playwright"), "text"),
        (McpGetCommand(name="playwright", output_format="json"), "json"),
        (McpAddCommand(name="playwright"), "text"),
        (McpRemoveCommand(name="playwright"), "text"),
        (McpSetEnabledCommand(name="playwright", enabled=True), "text"),
    ),
)
def test_each_cli_command_resolves_its_output_mode(
    command: CliCommand,
    expected: OutputMode,
) -> None:
    assert resolve_cli_output_mode(command) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "start_error",
    [None, RuntimeError("config service failed")],
    ids=["ready", "start-failed"],
)
async def test_agent_listen_owns_config_service_lifecycle(
    monkeypatch,
    tmp_path,
    start_error,
) -> None:
    tui_runtime = TuiRuntime()
    tui_runtime.open = AsyncMock()
    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=Mock()),
        runtime=tui_runtime,
    )
    controller = SimpleNamespace(
        frontend=frontend,
        service_runtime=SimpleNamespace(bind=Mock()),
        external_mcp=SimpleNamespace(current=None),
        is_service_mcp_linked=lambda: False,
        set_history_workspace=Mock(),
        exit_code=0,
    )
    preference = SimpleNamespace(load_pref=AsyncMock())
    config_session = SimpleNamespace()
    config_service = SimpleNamespace(
        start=AsyncMock(side_effect=start_error),
        stop=AsyncMock(),
    )
    config_service_factory = Mock(return_value=config_service)

    monkeypatch.setattr(
        "server.ConfigServiceRuntime",
        config_service_factory,
    )
    server_calls = []
    monkeypatch.setattr(
        bootstrap,
        "ServerManage",
        lambda *args, **kwargs: server_calls.append((args, kwargs)) or object(),
    )
    monkeypatch.setattr(bootstrap, "process_env", lambda: {})
    monkeypatch.setattr(
        bootstrap,
        "fetch_runtime_workspace_root",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        bootstrap,
        "ServiceConfig",
        lambda _session: SimpleNamespace(
            load_domain=AsyncMock(return_value="https://example.test"),
        ),
    )
    monkeypatch.setattr(bootstrap.service_endpoints, "configure", Mock())
    monkeypatch.setattr(bootstrap, "run_selected_command", AsyncMock())
    monkeypatch.setattr(bootstrap, "finalize_application", AsyncMock())
    monkeypatch.setattr(
        "frontends.tui.session.state.preload_tui_prompt_context",
        AsyncMock(),
    )
    monkeypatch.setattr(bootstrap, "start_tui_external_mcp", AsyncMock())
    confirm_helix = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "frontends.tui.features.helix.confirm_tui_service_runtime_startup",
        confirm_helix,
    )
    report = SimpleNamespace(close=Mock())

    operation = bootstrap._run_controller(
        AgentListenCommand(helix_profile="api"),
        frontend=frontend,
        design=None,
        animation=SimpleNamespace(),
        home=tmp_path,
        reports=tmp_path,
        preference=preference,
        config_session=config_session,
        report=report,
        runtime_spec=SimpleNamespace(
            launch_command=[],
            working_directory=str(tmp_path),
        ),
        service_context=SimpleNamespace(),
        power=1,
        output_mode="tui",
        permissions=preset_permissions("auto"),
        application_host_factory=lambda *_args, **_kwargs: controller,
    )

    if start_error is None:
        await operation
    else:
        with pytest.raises(RuntimeError, match="config service failed"):
            await operation

    config_service_factory.assert_called_once_with(
        config_session,
        log_level=const.SHOW_LEVEL,
    )
    config_service.start.assert_awaited_once_with()
    config_service.stop.assert_awaited_once_with()
    if start_error is None:
        confirm_helix.assert_awaited_once_with(controller)
    else:
        confirm_helix.assert_not_awaited()
    assert server_calls == [
        (([],), {"env": {}, "cwd": str(tmp_path)}),
    ]
    report.close.assert_called_once_with()


@pytest.mark.anyio
async def test_run_controller_closes_report_when_initialization_fails(
    monkeypatch,
    tmp_path,
) -> None:
    report = SimpleNamespace(close=Mock())

    monkeypatch.setattr(
        bootstrap,
        "ServerManage",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(bootstrap, "process_env", lambda: {})

    def fail_controller(*_args, **_kwargs):
        raise RuntimeError("controller failed")

    with pytest.raises(RuntimeError, match="controller failed"):
        await bootstrap._run_controller(
            AgentListenCommand(),
            frontend=SimpleNamespace(),
            design=None,
            animation=SimpleNamespace(),
            home=tmp_path,
            reports=tmp_path,
            preference=SimpleNamespace(),
            config_session=SimpleNamespace(),
            report=report,
            runtime_spec=SimpleNamespace(
                launch_command=[],
                working_directory=str(tmp_path),
            ),
            service_context=SimpleNamespace(),
            power=1,
            output_mode="text",
            permissions=preset_permissions("auto"),
            application_host_factory=fail_controller,
        )

    report.close.assert_called_once_with()


def test_doctor_json_uses_application_json_sink(monkeypatch) -> None:
    stream = StringIO()
    monkeypatch.setattr(cli_frontend.sys, "stdout", stream)

    frontend = resolve_cli_frontend(
        resolve_cli_output_mode(DoctorCommand(output_format="json"))
    )

    assert isinstance(frontend.application, JsonApplicationSink)


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty"),
    ((False, False), (False, True), (True, False)),
)
def test_tui_frontend_requires_interactive_terminal(
    monkeypatch,
    stdin_tty: bool,
    stdout_tty: bool,
) -> None:
    monkeypatch.setattr(
        cli_frontend.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: stdin_tty),
    )
    monkeypatch.setattr(
        cli_frontend.sys,
        "stdout",
        SimpleNamespace(isatty=lambda: stdout_tty),
    )

    with pytest.raises(AppError, match="interactive stdin and stdout"):
        resolve_cli_frontend("tui")


@pytest.mark.anyio
async def test_upgrade_entry_downloads_and_exits_without_opening_runtime(
    monkeypatch,
    tmp_path,
) -> None:
    command = RuntimeUpgradeCommand()
    application_views = []
    upgrade_calls = []
    design = object()
    report = SimpleNamespace(close=Mock(), run_id="test-run")

    class RuntimeStub(object):
        async def open(self) -> None:
            raise AssertionError("upgrade must not open a frontend runtime")

        async def close(self) -> None:
            raise AssertionError("upgrade must not close an unopened runtime")

    frontend = SimpleNamespace(
        application=SimpleNamespace(
            emit=lambda view: application_views.append(view.type),
        ),
        runtime=RuntimeStub(),
    )
    runtime_spec = SimpleNamespace(
        executable=str(tmp_path / "helix.exe"),
        supports=str(tmp_path),
        launch_command=[],
    )
    app_layout = ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )

    async def ensure_upgrade(context, **kwargs) -> bool:
        upgrade_calls.append((context, kwargs))
        return True

    monkeypatch.setattr(bootstrap, "observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "RunReport", lambda _path: report)
    monkeypatch.setattr(bootstrap, "resolve_cli_frontend", lambda _mode: frontend)
    monkeypatch.setattr(bootstrap, "resolve_cli_design", lambda _frontend, _mode: design)
    monkeypatch.setattr(bootstrap, "resolve_application_layout", lambda **_kwargs: app_layout)
    monkeypatch.setattr(bootstrap, "ensure_mind_home", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "mind_reports_dir", lambda: tmp_path / "reports")
    monkeypatch.setattr(bootstrap, "mind_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(bootstrap, "Preferences", lambda _path: object())
    monkeypatch.setattr(bootstrap, "resolve_service_runtime", lambda **_kwargs: runtime_spec)
    monkeypatch.setattr(bootstrap, "route_shell_tools", lambda _supports: None)
    monkeypatch.setattr(bootstrap, "ensure_service_runtime_asset", ensure_upgrade)
    result = await bootstrap._run_application(
        command,
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
        runtime_services=_runtime_services(),
    )

    assert result == 0
    assert application_views == []
    assert len(upgrade_calls) == 1
    _context, kwargs = upgrade_calls[0]
    assert kwargs["explicit_upgrade"] is True
    assert kwargs["design"] is design
    assert "progress" not in kwargs
    report.close.assert_called_once_with()


@pytest.mark.anyio
@pytest.mark.parametrize("exit_code", (0, 130))
async def test_tui_finalization_prints_summary_after_cleanup(
    exit_code: int,
) -> None:
    events = []
    runtime = TuiRuntime()
    runtime.close = AsyncMock(side_effect=lambda: events.append("runtime"))
    runtime.print_exit_summary = Mock(
        side_effect=lambda *_args, **_kwargs: events.append("summary"),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        await_cleanup=_await_cleanup,
        end_conversation=AsyncMock(
            side_effect=lambda **_kwargs: events.append("session"),
        ),
        close_runtime_resources=AsyncMock(
            side_effect=lambda: events.append("resources"),
        ),
        conversation=SimpleNamespace(
            turn_count=1,
            sid="sid_test_1_abcdef",
        ),
        exit_code=exit_code,
    )

    await bootstrap.finalize_application(
        mind,
        output_mode="tui",
        completed=True,
    )

    assert events == ["session", "runtime", "resources", "summary"]
    mind.end_conversation.assert_awaited_once_with(reason="exit")
    runtime.print_exit_summary.assert_called_once_with("sid_test_1_abcdef")


@pytest.mark.anyio
async def test_tui_finalization_closes_silently_without_a_conversation() -> None:
    events = []
    runtime = TuiRuntime()
    runtime.close = AsyncMock(side_effect=lambda: events.append("runtime"))
    runtime.print_exit_summary = Mock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        await_cleanup=_await_cleanup,
        end_conversation=AsyncMock(
            side_effect=lambda **_kwargs: events.append("session"),
        ),
        close_runtime_resources=AsyncMock(
            side_effect=lambda: events.append("resources"),
        ),
        conversation=SimpleNamespace(turn_count=0, sid=None),
        exit_code=0,
    )

    await bootstrap.finalize_application(
        mind,
        output_mode="tui",
        completed=True,
    )

    assert events == ["session", "runtime", "resources"]
    runtime.print_exit_summary.assert_not_called()


@pytest.mark.anyio
async def test_tui_finalization_skips_summary_for_incomplete_session() -> None:
    runtime = TuiRuntime()
    runtime.close = AsyncMock()
    runtime.print_exit_summary = Mock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        await_cleanup=_await_cleanup,
        end_conversation=AsyncMock(),
        close_runtime_resources=AsyncMock(),
        exit_code=0,
    )

    await bootstrap.finalize_application(
        mind,
        output_mode="tui",
        completed=False,
    )

    runtime.close.assert_awaited_once_with()
    mind.end_conversation.assert_awaited_once_with(reason="error")
    mind.close_runtime_resources.assert_awaited_once_with()
    runtime.print_exit_summary.assert_not_called()


@pytest.mark.anyio
async def test_tui_finalization_skips_summary_when_cleanup_fails() -> None:
    runtime = TuiRuntime()
    runtime.close = AsyncMock(side_effect=RuntimeError("close failed"))
    runtime.print_exit_summary = Mock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        await_cleanup=_await_cleanup,
        end_conversation=AsyncMock(),
        close_runtime_resources=AsyncMock(),
        exit_code=0,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        await bootstrap.finalize_application(
            mind,
            output_mode="tui",
            completed=True,
        )

    mind.close_runtime_resources.assert_awaited_once_with()
    runtime.print_exit_summary.assert_not_called()


@pytest.mark.anyio
async def test_finalization_closes_resources_when_session_end_fails() -> None:
    runtime = TuiRuntime()
    runtime.close = AsyncMock()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        await_cleanup=_await_cleanup,
        end_conversation=AsyncMock(
            side_effect=RuntimeError("session end failed"),
        ),
        close_runtime_resources=AsyncMock(),
        exit_code=1,
    )

    with pytest.raises(RuntimeError, match="session end failed"):
        await bootstrap.finalize_application(
            mind,
            output_mode="text",
            completed=False,
        )

    runtime.close.assert_awaited_once_with()
    mind.close_runtime_resources.assert_awaited_once_with()
