# -*- coding: utf-8 -*-

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from mind_app.cli import bootstrap
from mind_app.cli.commands import (
    AgentListenCommand,
    ExecCommand,
    InteractiveCommand,
)
from mind_app.cli.bootstrap import _confirm_tui_project_trust
from mind_app.frontend.sinks import (
    ConsoleApplicationSink,
    JsonApplicationSink,
)
from mind_app.runtime.mcp.service_runtime import ServiceRuntimeSpec
from mind_app.tui.adapters.hooks import TuiHookStatusAdapter
from mind_app.tui.core.runtime import TuiRuntime
from mind_core.application_paths import ApplicationLayout
from mind_core.config_layers import PROJECT_CONFIG_DIR
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore


class _TrustRuntime(object):
    def __init__(self, *decisions: bool) -> None:
        self.decisions = list(decisions)
        self.started: tuple[Path, Path] | None = None
        self.errors: list[str] = []

    async def begin_directory_trust(
        self,
        cwd: Path,
        trust_target: Path,
    ) -> None:
        self.started = (cwd, trust_target)

    async def wait_directory_trust(self) -> bool:
        return self.decisions.pop(0)

    def show_directory_trust_error(self, message: str) -> None:
        self.errors.append(message)


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    return project_root, workspace, project_config


def _patch_application_bootstrap(
    monkeypatch,
    tmp_path: Path,
    *,
    frontend,
    config_path: Path,
) -> tuple[Mock, AsyncMock]:
    report = SimpleNamespace(close=Mock())
    run_controller = AsyncMock(return_value=0)
    app_layout = ApplicationLayout(
        mode="source",
        platform="darwin",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "supports",
    )
    runtime_spec = ServiceRuntimeSpec(
        supports=str(app_layout.supports),
        executable=str(tmp_path / "service"),
        launch_command=[],
        path_entries=(),
        working_directory=str(tmp_path),
    )

    monkeypatch.setattr(bootstrap, "resolve_cli_frontend", lambda _mode: frontend)
    monkeypatch.setattr(bootstrap, "resolve_cli_design", lambda *_args: None)
    monkeypatch.setattr(
        bootstrap,
        "resolve_application_layout",
        lambda **_kwargs: app_layout,
    )
    monkeypatch.setattr(bootstrap, "resolve_service_runtime", lambda **_kwargs: runtime_spec)
    monkeypatch.setattr(bootstrap, "ensure_mind_home", lambda: tmp_path / "home")
    monkeypatch.setattr(bootstrap, "mind_reports_dir", lambda: tmp_path / "reports")
    monkeypatch.setattr(bootstrap, "mind_config_path", lambda: config_path)
    monkeypatch.setattr(bootstrap, "RunReport", lambda _path: report)
    monkeypatch.setattr(bootstrap, "Preferences", lambda _session: object())
    monkeypatch.setattr(bootstrap, "route_shell_tools", lambda _supports: None)
    monkeypatch.setattr(bootstrap, "clear_exec_env_cache", lambda: None)
    monkeypatch.setattr(bootstrap, "observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bootstrap,
        "observe_exception",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bootstrap, "_run_controller", run_controller)
    return report, run_controller


@pytest.mark.anyio
async def test_accepting_unknown_project_reloads_all_project_automation(
    tmp_path,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        'sandbox_mode = "read-only"\n'
        'approval_policy = "never"\n'
        "[tui.keymap.global]\n"
        'open_transcript = "f12"\n'
        "[agents]\n"
        "max_depth = 3\n"
        "[mcp_servers.project]\n"
        'command = "project-server"\n'
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "project-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    initial = session.resolve()
    runtime = _TrustRuntime(True)

    assert "project" not in initial.config["mcp_servers"]
    assert initial.hooks == ()
    assert initial.config["agents"]["max_depth"] == 1
    assert initial.config["sandbox_mode"] == ""

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=initial,
        workspace=workspace,
    )

    assert result is not None
    assert result.project_trust is not None
    assert result.project_trust.level == "trusted"
    assert result.config["mcp_servers"]["project"]["command"] == "project-server"
    assert result.config["agents"]["max_depth"] == 3
    assert result.config["sandbox_mode"] == "read-only"
    assert result.config["approval_policy"] == "never"
    assert "tui" in result.startup_warnings[0]
    assert len(result.hooks) == 1
    assert store.read_raw()["projects"][str(project_root.resolve())] == {
        "trust_level": "trusted",
    }
    assert runtime.started == (workspace, project_root.resolve())
    assert runtime.errors == []


@pytest.mark.anyio
async def test_accepting_linked_worktree_trusts_main_repository(tmp_path) -> None:
    repository_root = tmp_path / "repository"
    git_dir = repository_root / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree_root = tmp_path / "worktree"
    workspace = worktree_root / "src"
    workspace.mkdir(parents=True)
    (worktree_root / ".git").write_text(
        f"gitdir: {git_dir}\n",
        encoding="utf-8",
    )
    project_config = worktree_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    initial = session.resolve()
    runtime = _TrustRuntime(True)

    assert initial.project_trust is not None
    assert initial.project_trust.project_root == worktree_root.resolve()
    assert initial.project_trust.trust_root == repository_root.resolve()

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=initial,
        workspace=workspace,
    )

    assert result is not None
    assert result.project_trust is not None
    assert result.project_trust.project_root == worktree_root.resolve()
    assert result.project_trust.trust_root == repository_root.resolve()
    assert result.project_trust.level == "trusted"
    assert result.config["mcp_servers"]["project"]["command"] == (
        "project-server"
    )
    projects = store.read_raw()["projects"]
    assert projects[str(repository_root.resolve())] == {
        "trust_level": "trusted",
    }
    assert str(worktree_root.resolve()) not in projects
    assert runtime.started == (workspace, repository_root.resolve())
    assert runtime.errors == []


@pytest.mark.anyio
async def test_accepting_home_directory_does_not_reclassify_user_config(
    tmp_path,
) -> None:
    workspace = tmp_path / "home"
    (workspace / ".git").mkdir(parents=True)
    store = ConfigStore(workspace / PROJECT_CONFIG_DIR / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    initial = session.resolve()
    runtime = _TrustRuntime(True)

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=initial,
        workspace=workspace,
    )

    assert result is not None
    assert result.project_trust is not None
    assert result.project_trust.level == "trusted"
    assert [layer.scope for layer in result.layers] == ["user"]
    assert store.read_raw()["projects"][str(workspace.resolve())] == {
        "trust_level": "trusted",
    }
    assert runtime.errors == []


@pytest.mark.anyio
async def test_declining_unknown_project_does_not_persist_a_decision(
    tmp_path,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    initial = session.resolve()
    before = store.path.read_text(encoding="utf-8")
    runtime = _TrustRuntime(False)

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=initial,
        workspace=workspace,
    )

    assert result is None
    assert store.path.read_text(encoding="utf-8") == before
    assert session.resolve().project_trust is not None
    assert session.resolve().project_trust.level is None


@pytest.mark.anyio
async def test_invalid_project_config_stays_untrusted_and_keeps_prompt_open(
    tmp_path,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        'unknown_project_field = true\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    initial = session.resolve()
    before = store.path.read_text(encoding="utf-8")
    runtime = _TrustRuntime(True, False)

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=initial,
        workspace=workspace,
    )

    assert result is None
    assert store.path.read_text(encoding="utf-8") == before
    assert session.resolve().project_trust is not None
    assert session.resolve().project_trust.level is None
    assert len(runtime.errors) == 1
    assert "unknown_project_field" in runtime.errors[0]
    assert str(project_root.resolve()) in runtime.errors[0]


@pytest.mark.anyio
@pytest.mark.parametrize("trust_level", ("trusted", "untrusted"))
async def test_known_project_trust_skips_startup_prompt(
    tmp_path,
    trust_level: str,
) -> None:
    project_root, workspace, _project_config = _project(tmp_path)
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": trust_level},
    })
    session = ConfigSession(store, workspace=workspace)
    resolution = session.resolve()
    runtime = _TrustRuntime()

    result = await _confirm_tui_project_trust(
        runtime=runtime,
        config_session=session,
        resolution=resolution,
        workspace=workspace,
    )

    assert result is resolution
    assert runtime.started is None


@pytest.mark.anyio
async def test_tui_bootstrap_builds_controller_only_from_post_trust_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        'sandbox_mode = "read-only"\n'
        'approval_policy = "never"\n'
        "[tui.keymap.global]\n"
        'open_transcript = "f12"\n'
        "[agents]\n"
        "max_depth = 4\n"
        "[features]\n"
        "js_repl = false\n"
        "subagents = false\n"
        "[mcp_servers.project]\n"
        'command = "project-server"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    runtime = TuiRuntime()
    application = runtime.screen.application
    runtime.begin_directory_trust = AsyncMock()
    runtime.close = AsyncMock()
    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=Mock()),
        runtime=runtime,
    )
    config_path = tmp_path / "home" / "config.toml"
    _report, run_controller = _patch_application_bootstrap(
        monkeypatch,
        tmp_path,
        frontend=frontend,
        config_path=config_path,
    )

    async def accept_after_controller_check() -> bool:
        assert run_controller.await_count == 0
        return True

    runtime.wait_directory_trust = AsyncMock(
        side_effect=accept_after_controller_check,
    )

    result = await bootstrap._run_application(
        InteractiveCommand(),
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
    )

    assert result == 0
    assert runtime.screen.application is application
    runtime.begin_directory_trust.assert_awaited_once_with(
        workspace.resolve(),
        project_root.resolve(),
    )
    run_controller.assert_awaited_once()
    arguments = run_controller.await_args.kwargs
    assert arguments["agent_settings"].max_depth == 4
    assert arguments["feature_settings"].js_repl is False
    assert arguments["feature_settings"].subagents is False
    assert arguments["permissions"].sandbox_mode == "read-only"
    assert arguments["permissions"].approval_policy == "never"
    assert "tui" in arguments["startup_warnings"][0]
    resolution = arguments["config_session"].resolve()
    assert resolution.config["mcp_servers"]["project"]["command"] == (
        "project-server"
    )


@pytest.mark.anyio
async def test_bootstrap_forwards_hook_warnings_outside_config_resolution(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    frontend = SimpleNamespace(application=SimpleNamespace(emit=Mock()))
    config_path = tmp_path / "home" / "config.toml"
    ConfigStore(config_path).ensure()
    (config_path.parent / "hooks.json").write_text(
        "{broken",
        encoding="utf-8",
    )
    _report, run_controller = _patch_application_bootstrap(
        monkeypatch,
        tmp_path,
        frontend=frontend,
        config_path=config_path,
    )

    result = await bootstrap._run_application(
        ExecCommand("check hooks"),
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
    )

    assert result == 0
    run_controller.assert_awaited_once()
    arguments = run_controller.await_args.kwargs
    resolution = arguments["config_session"].resolve()
    assert resolution.startup_warnings == ()
    assert resolution.project_trust_warnings == ()
    assert len(resolution.hook_warnings) == 1
    assert arguments["startup_warnings"] == resolution.hook_warnings


def test_startup_config_warnings_are_emitted_as_styled_application_views() -> None:
    emit = Mock()
    frontend = SimpleNamespace(application=SimpleNamespace(emit=emit))
    bootstrap._emit_startup_warnings(frontend, (
        "ignored first project setting",
        "ignored second project setting",
    ))

    emit.assert_called_once()
    view = emit.call_args.args[0]
    assert view.type == "config.warning"
    assert view.renderable.plain_text == (
        "Warning: ignored first project setting\n"
        "Warning: ignored second project setting"
    )


@pytest.mark.anyio
async def test_tui_startup_warning_is_emitted_after_context_preload(
    monkeypatch,
    tmp_path,
) -> None:
    events = []
    controller_arguments = {}
    runtime = TuiRuntime()
    runtime.open = AsyncMock(side_effect=lambda: events.append("open"))
    frontend = SimpleNamespace(
        application=SimpleNamespace(
            emit=lambda _view: events.append("warning"),
        ),
        runtime=runtime,
    )
    controller = SimpleNamespace(
        frontend=frontend,
        bind_server_manager=Mock(),
        bind_service_runtime_context=Mock(),
        start_config_service=AsyncMock(),
        start_external_mcp_runtime=AsyncMock(),
        external_mcp=None,
        is_service_mcp_linked=lambda: False,
        set_history_workspace=Mock(),
        exit_code=0,
    )
    preference = SimpleNamespace(load_pref=AsyncMock())

    def build_controller(*_args, **kwargs):
        controller_arguments.update(kwargs)
        return controller

    monkeypatch.setattr(bootstrap, "Mind", build_controller)
    monkeypatch.setattr(bootstrap, "ServerManage", lambda *_args, **_kwargs: object())
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
            load_domain=AsyncMock(return_value=""),
        ),
    )
    monkeypatch.setattr(bootstrap.service_endpoints, "configure", Mock())
    monkeypatch.setattr(bootstrap, "run_selected_command", AsyncMock())
    monkeypatch.setattr(bootstrap, "finalize_application", AsyncMock())
    monkeypatch.setattr(bootstrap, "start_tui_external_mcp", AsyncMock())

    from mind_app.tui.session import state as tui_state

    monkeypatch.setattr(
        tui_state,
        "preload_tui_prompt_context",
        AsyncMock(side_effect=lambda _controller: events.append("preload")),
    )

    await bootstrap._run_controller(
        InteractiveCommand(),
        frontend=frontend,
        design=None,
        animation=SimpleNamespace(),
        home=tmp_path,
        reports=tmp_path,
        preference=preference,
        config_session=SimpleNamespace(),
        report=SimpleNamespace(close=Mock()),
        runtime_spec=SimpleNamespace(
            launch_command=[],
            working_directory=str(tmp_path),
        ),
        service_context=SimpleNamespace(),
        power=1,
        output_mode="tui",
        permissions=SimpleNamespace(),
        startup_warnings=("ignored project setting",),
    )

    assert events[:3] == ["preload", "warning", "open"]
    assert isinstance(
        controller_arguments["hook_status"],
        TuiHookStatusAdapter,
    )


@pytest.mark.anyio
async def test_tui_bootstrap_quit_stops_before_controller_and_config_service(
    monkeypatch,
    tmp_path,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    runtime = TuiRuntime()
    runtime.begin_directory_trust = AsyncMock()
    runtime.wait_directory_trust = AsyncMock(return_value=False)
    runtime.close = AsyncMock()
    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=Mock()),
        runtime=runtime,
    )
    config_path = tmp_path / "home" / "config.toml"
    report, run_controller = _patch_application_bootstrap(
        monkeypatch,
        tmp_path,
        frontend=frontend,
        config_path=config_path,
    )

    result = await bootstrap._run_application(
        InteractiveCommand(),
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
    )

    assert result == 0
    run_controller.assert_not_awaited()
    runtime.close.assert_awaited_once_with()
    report.close.assert_called_once_with()
    trust = ConfigSession(
        ConfigStore(config_path),
        workspace=workspace,
    ).resolve().project_trust
    assert trust is not None
    assert trust.level is None


@pytest.mark.anyio
async def test_noninteractive_exec_never_requests_directory_trust(
    monkeypatch,
    tmp_path,
) -> None:
    _project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=Mock()),
        runtime=SimpleNamespace(),
    )
    config_path = tmp_path / "home" / "config.toml"
    _report, run_controller = _patch_application_bootstrap(
        monkeypatch,
        tmp_path,
        frontend=frontend,
        config_path=config_path,
    )
    confirm = AsyncMock()
    monkeypatch.setattr(bootstrap, "_confirm_tui_project_trust", confirm)

    result = await bootstrap._run_application(
        ExecCommand(prompt="inspect"),
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
    )

    assert result == 0
    confirm.assert_not_awaited()
    run_controller.assert_awaited_once()
    arguments = run_controller.await_args.kwargs
    assert arguments["agent_settings"].max_depth == 1
    assert "project" not in (
        arguments["config_session"].resolve().config["mcp_servers"]
    )
    assert len(arguments["startup_warnings"]) == 1
    warning = arguments["startup_warnings"][0]
    assert "Skipped" in warning
    assert str(project_config.parent.resolve()) in warning
    assert str(_project_root.resolve()) in warning


@pytest.mark.anyio
async def test_agent_listen_uses_tui_directory_trust(
    monkeypatch,
    tmp_path,
) -> None:
    _project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=Mock()),
        runtime=TuiRuntime(),
    )
    _report, run_controller = _patch_application_bootstrap(
        monkeypatch,
        tmp_path,
        frontend=frontend,
        config_path=tmp_path / "home" / "config.toml",
    )

    async def accept_project(**kwargs):
        resolution = kwargs["resolution"]
        return kwargs["config_session"].set_project_trust(
            resolution.project_trust,
            "trusted",
        )

    confirm = AsyncMock(side_effect=accept_project)
    monkeypatch.setattr(bootstrap, "_confirm_tui_project_trust", confirm)

    result = await bootstrap._run_application(
        AgentListenCommand(),
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
        (),
        None,
    )

    assert result == 0
    confirm.assert_awaited_once()
    arguments = run_controller.await_args.kwargs
    assert arguments["startup_warnings"] == ()
    resolution = arguments["config_session"].resolve()
    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "trusted"
    assert resolution.config["mcp_servers"]["project"]["command"] == (
        "project-server"
    )


@pytest.mark.parametrize("trust_level", (None, "untrusted"))
def test_untrusted_project_automation_is_skipped_with_warning(
    tmp_path,
    trust_level: str | None,
) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text("[broken\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    if trust_level is not None:
        store.update({
            ("projects", str(project_root)): {"trust_level": trust_level},
        })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.hooks == ()
    project_layer = next(
        layer for layer in resolution.layers if layer.scope == "project"
    )
    assert not project_layer.enabled
    assert resolution.startup_warnings == ()
    assert len(resolution.project_trust_warnings) == 1
    warning = resolution.project_trust_warnings[0]
    assert str(project_config.parent.resolve()) in warning
    assert "project-local config, hooks, and exec policies" in warning
    if trust_level == "untrusted":
        assert "marked as untrusted" in warning
    else:
        assert "add" in warning


def test_trusted_project_automation_has_no_skip_warning(tmp_path) -> None:
    project_root, workspace, project_config = _project(tmp_path)
    project_config.write_text(
        '[mcp_servers.project]\ncommand = "project-server"\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.startup_warnings == ()
    assert resolution.project_trust_warnings == ()
    assert resolution.config["mcp_servers"]["project"]["command"] == (
        "project-server"
    )


def test_startup_warning_uses_stderr_and_structured_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    human = ConsoleApplicationSink(
        Console(file=stdout, force_terminal=False),
        Console(file=stderr, force_terminal=False),
    )

    bootstrap._emit_startup_warnings(
        SimpleNamespace(application=human),
        ("project automation was skipped",),
        process_output=True,
    )

    assert stdout.getvalue() == ""
    assert "Warning: project automation was skipped" in stderr.getvalue()

    output = io.StringIO()
    structured = JsonApplicationSink(output)
    bootstrap._emit_startup_warnings(
        SimpleNamespace(application=structured),
        ("project automation was skipped",),
        process_output=True,
    )

    assert json.loads(output.getvalue()) == {
        "type": "config.warning",
        "message": "project automation was skipped",
    }


if __name__ == '__main__':
    pass
