# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.cli import bootstrap
from mind_app.cli.commands import ExecCommand, InteractiveCommand
from mind_app.cli.bootstrap import _confirm_tui_project_trust
from mind_app.runtime.mcp.service_runtime import ServiceRuntimeSpec
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
        '[tui.keymap.global]\nopen_transcript = "f12"\n',
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
    assert "cannot override tui" in runtime.errors[0]
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
        "[agents]\n"
        "max_depth = 4\n"
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
    resolution = arguments["config_session"].resolve()
    assert resolution.config["mcp_servers"]["project"]["command"] == (
        "project-server"
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


if __name__ == '__main__':
    pass
