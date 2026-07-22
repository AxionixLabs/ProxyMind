# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from mind_app.cli import entry
from mind_app.cli.frontend import (
    resolve_cli_design,
    resolve_cli_frontend,
)
from mind_app.cli.selection import resolve_cli_output_mode
from mind_app.frontend.contracts import PassiveFrontendRuntime
from mind_app.frontend.sinks import ConsoleApplicationSink
from mind_core.parser import Parser


def test_gravity_option_is_removed() -> None:
    parser = Parser().parse_engine

    assert parser is not None
    assert "--gravity" not in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["--gravity", "archive"])


def test_upgrade_uses_rich_frontend_without_tui_runtime() -> None:
    command = SimpleNamespace(
        upgrade=True,
        json=False,
        agent=False,
        chat=None,
        fast=None,
        xtra=None,
        code=None,
    )

    output_mode = resolve_cli_output_mode(command)
    frontend = resolve_cli_frontend(output_mode)
    design = resolve_cli_design(frontend, output_mode)

    assert output_mode == "rich"
    assert isinstance(frontend.application, ConsoleApplicationSink)
    assert isinstance(frontend.runtime, PassiveFrontendRuntime)
    assert design is not None


@pytest.mark.anyio
async def test_upgrade_entry_downloads_and_exits_without_opening_runtime(
    monkeypatch,
    tmp_path,
) -> None:
    command = SimpleNamespace(
        upgrade=True,
        json=False,
        agent=False,
        chat=None,
        fast=None,
        xtra=None,
        code=None,
    )
    application_views = []
    upgrade_calls = []
    design = object()

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

    async def ensure_upgrade(context, **kwargs) -> bool:
        upgrade_calls.append((context, kwargs))
        return True

    monkeypatch.setattr(entry.Active, "silent", lambda: None)
    monkeypatch.setattr(
        entry,
        "Parser",
        lambda: SimpleNamespace(parse_cmd=command),
    )
    monkeypatch.setattr(entry, "resolve_cli_frontend", lambda _mode: frontend)
    monkeypatch.setattr(entry, "resolve_cli_design", lambda _frontend, _mode: design)
    monkeypatch.setattr(entry, "ensure_mind_home", lambda: tmp_path)
    monkeypatch.setattr(entry, "mind_reports_dir", lambda: tmp_path / "reports")
    monkeypatch.setattr(entry, "mind_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(entry, "ensure_mcp_servers_file", lambda: None)
    monkeypatch.setattr(entry, "Preferences", lambda _path: object())
    monkeypatch.setattr(entry, "resolve_service_runtime", lambda **_kwargs: runtime_spec)
    monkeypatch.setattr(entry, "route_shell_tools", lambda _supports: None)
    monkeypatch.setattr(entry, "clear_exec_env_cache", lambda: None)
    monkeypatch.setattr(entry, "ensure_service_runtime_asset", ensure_upgrade)
    monkeypatch.setattr(
        entry.sys,
        "argv",
        [str(tmp_path / "mind.py"), "--upgrade"],
    )

    result = await entry._run_main(
        str(tmp_path / "mind.py"),
        SimpleNamespace(),
    )

    assert result == 0
    assert application_views == ["intro"]
    assert len(upgrade_calls) == 1
    _context, kwargs = upgrade_calls[0]
    assert kwargs["explicit_upgrade"] is True
    assert kwargs["design"] is design
    assert "progress" not in kwargs
