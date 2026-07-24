# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.cli import entry
from mind_app.cli.commands import DoctorCommand
from mind_app.cli.doctor import (
    DoctorCheck,
    DoctorContext,
    DoctorReport,
    diagnose,
)
from mind_app.runtime.mcp.service_runtime import ServiceRuntimeSpec
from mind_core.application_paths import ApplicationLayout


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
        mcp_config_path=home / "mcp_servers.json",
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
    context.mcp_config_path.write_text("{invalid", encoding="utf-8")

    report = diagnose(context)

    checks = {check.key: check for check in report.checks}
    assert report.exit_code == 1
    assert checks["external_mcp"].status == "fail"
    assert "invalid" in checks["external_mcp"].summary


@pytest.mark.anyio
async def test_doctor_entry_skips_runtime_bootstrap(monkeypatch, tmp_path) -> None:
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

    monkeypatch.setattr(entry.logger, "remove", lambda: None)
    monkeypatch.setattr(entry, "parse_cli_command", lambda: DoctorCommand())
    monkeypatch.setattr(entry, "resolve_cli_frontend", lambda _mode: frontend)
    monkeypatch.setattr(entry, "resolve_cli_design", lambda *_args: object())
    monkeypatch.setattr(entry, "resolve_application_layout", lambda **_kwargs: app_layout)
    monkeypatch.setattr(entry, "resolve_service_runtime", lambda **_kwargs: runtime_spec)
    monkeypatch.setattr(entry, "diagnose", diagnose_mock)
    monkeypatch.setattr(entry, "mind_home", lambda: tmp_path / ".mind")
    monkeypatch.setattr(entry, "mind_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(
        entry,
        "mind_mcp_servers_path",
        lambda: tmp_path / "mcp_servers.json",
    )
    monkeypatch.setattr(
        entry,
        "ensure_mind_home",
        Mock(side_effect=AssertionError("doctor created Mind home")),
    )
    monkeypatch.setattr(
        entry,
        "RunReport",
        Mock(side_effect=AssertionError("doctor created a run report")),
    )
    result = await entry._run_main(
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
    )

    assert result == 0
    assert len(emitted) == 1
    assert emitted[0].type == "doctor"
    diagnose_mock.assert_called_once()
