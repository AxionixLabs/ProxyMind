# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.cli import doctor
from mind_app.cli.commands import DoctorCommand
from mind_app.cli.doctor import (
    DoctorCheck,
    DoctorContext,
    DoctorReport,
    diagnose,
)
from mind_app.runtime.mcp.service_runtime import ServiceRuntimeSpec
from infrastructure.config.paths import ApplicationLayout
from infrastructure.hooks.discovery import HOOKS_FILE_NAME
from mind_core.config_store import ConfigStore
from mind_core.config_layers import PROJECT_CONFIG_DIR


def _doctor_context(tmp_path, *, packaged: bool = False) -> DoctorContext:
    home = tmp_path / ".mind"
    runtime_spec = ServiceRuntimeSpec(
        supports=str(tmp_path / "supports"),
        executable=str(tmp_path / "supports" / "helix.exe"),
        launch_command=["python", "-m", "backend.helix"],
        path_entries=(),
    )
    return DoctorContext(
        platform="win32",
        entry_mode="source",
        entry_root=tmp_path,
        home=home,
        config_path=home / "config.toml",
        supports=tmp_path / "supports",
        packaged=packaged,
        runtime_spec=runtime_spec,
    )


def test_doctor_does_not_create_missing_home(tmp_path) -> None:
    context = _doctor_context(tmp_path)

    report = diagnose(context)

    assert report.exit_code == 0
    assert not context.home.exists()
    checks = {check.key: check for check in report.checks}
    assert checks["mind_home"].status == "warn"
    assert checks["config"].status == "warn"
    assert checks["external_mcp"].status == "warn"


def test_doctor_fails_for_invalid_mcp_config(tmp_path) -> None:
    context = _doctor_context(tmp_path)
    context.home.mkdir()
    context.config_path.write_text(
        "[mcp_servers.remote]\nenabled = true\n",
        encoding="utf-8",
    )

    report = diagnose(context)

    checks = {check.key: check for check in report.checks}
    assert report.exit_code == 1
    assert checks["external_mcp"].status == "fail"
    assert "invalid" in checks["external_mcp"].summary


def test_doctor_reports_linked_worktree_trust_root(
    monkeypatch,
    tmp_path,
) -> None:
    context = _doctor_context(tmp_path)
    repository_root = tmp_path / "repository"
    git_dir = repository_root / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    (worktree_root / ".git").write_text(
        f"gitdir: {git_dir}\n",
        encoding="utf-8",
    )
    ConfigStore(context.config_path).update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })
    monkeypatch.chdir(worktree_root)

    check = doctor._config_check(context)

    assert f"project={worktree_root.resolve()}" in check.detail
    assert f"trust={repository_root.resolve()}" in check.detail
    assert "trust_level=trusted" in check.detail


def test_doctor_reports_disabled_project_config_layer(
    monkeypatch,
    tmp_path,
) -> None:
    context = _doctor_context(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[agents]\nmax_depth = 2\n", encoding="utf-8")
    ConfigStore(context.config_path).ensure()
    monkeypatch.chdir(project_root)

    check = doctor._config_check(context)

    assert "layers=user > project(disabled)" in check.detail
    assert "trust_level=unknown" in check.detail
    assert "disabled_layers=1" in check.detail


def test_doctor_does_not_report_hook_warning_as_project_config_issue(
    monkeypatch,
    tmp_path,
) -> None:
    context = _doctor_context(tmp_path)
    ConfigStore(context.config_path).update({
        ("model_provider",): "openai-main",
        ("model_providers", "openai-main", "model"): "test-model",
    })
    (context.home / HOOKS_FILE_NAME).write_text(
        "{broken",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    check = doctor._config_check(context)

    assert check.status == "pass"
    assert "ignored user-level settings" not in check.summary
    assert "config_warnings=" not in check.detail


def test_doctor_entry_skips_runtime_bootstrap(monkeypatch, tmp_path) -> None:
    emitted = []
    frontend = SimpleNamespace(
        application=SimpleNamespace(emit=emitted.append),
    )
    runtime_spec = ServiceRuntimeSpec(
        supports=str(tmp_path / "supports"),
        executable=str(tmp_path / "helix.exe"),
        launch_command=["python", "-m", "backend.helix"],
        path_entries=(),
    )
    report = DoctorReport(checks=(
        DoctorCheck("platform", "Platform", "pass", "win32 is supported"),
    ))
    diagnose_mock = Mock(return_value=report)
    app_layout = ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )

    monkeypatch.setattr(doctor, "resolve_cli_frontend", lambda _mode: frontend)
    monkeypatch.setattr(doctor, "resolve_application_layout", lambda **_kwargs: app_layout)
    monkeypatch.setattr(doctor, "resolve_service_runtime", lambda **_kwargs: runtime_spec)
    monkeypatch.setattr(doctor, "diagnose", diagnose_mock)
    monkeypatch.setattr(doctor, "mind_home", lambda: tmp_path / ".mind")
    monkeypatch.setattr(doctor, "mind_config_path", lambda: tmp_path / "config.toml")
    result = doctor.run_doctor_command(
        DoctorCommand(),
        entry_file=str(tmp_path / "mind.py"),
    )

    assert result == 0
    assert len(emitted) == 1
    assert emitted[0].type == "doctor"
    diagnose_mock.assert_called_once()
