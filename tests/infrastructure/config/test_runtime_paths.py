# -*- coding: utf-8 -*-

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from infrastructure.config import runtime_paths
from infrastructure.config.paths import (
    CONFIG_HOME_ENV,
    STATE_HOME_ENV,
)
from infrastructure.config.store import ConfigStore
from infrastructure.errors import AppError
def test_runtime_directories_are_owned_by_state_home(
    monkeypatch,
    tmp_path,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setattr(runtime_paths, "state_home", lambda: state_root)

    assert runtime_paths.reports_dir() == state_root / "reports"
    assert runtime_paths.sessions_dir() == state_root / "sessions"
    assert runtime_paths.history_dir() == state_root / "history"
    assert runtime_paths.conversation_history_db_path() == (
        state_root / "history" / "history.db"
    )
    assert runtime_paths.agent_graph_db_path() == (
        state_root / "history" / "agents.db"
    )
    assert runtime_paths.effect_journal_db_path() == (
        state_root / "history" / "effects.db"
    )
    assert runtime_paths.approval_fact_db_path() == (
        state_root / "history" / "approvals.db"
    )
    assert runtime_paths.agent_runtime_db_path() == (
        state_root / "history" / "runtime.db"
    )


def test_process_environment_propagates_config_state_and_helix_roots(
    tmp_path,
) -> None:
    config_root = tmp_path / "config"
    state_root = tmp_path / "state"
    environment = {
        CONFIG_HOME_ENV: str(config_root),
        STATE_HOME_ENV: str(state_root),
    }

    assert runtime_paths.process_env(
        environment=environment,
        user_home=tmp_path,
    ) == {
        CONFIG_HOME_ENV: str(config_root),
        STATE_HOME_ENV: str(state_root),
        runtime_paths.HX_HOME_ENV: str(state_root / "helix"),
        runtime_paths.HELIX_STORAGE_ROOT_ENV: str(state_root / "helix"),
    }


def test_process_environment_preserves_explicit_helix_home(tmp_path) -> None:
    config_root = tmp_path / "config"
    state_root = tmp_path / "state"
    helix_root = tmp_path / "helix-explicit"
    environment = {
        CONFIG_HOME_ENV: str(config_root),
        STATE_HOME_ENV: str(state_root),
        runtime_paths.HX_HOME_ENV: str(helix_root),
    }

    propagated = runtime_paths.process_env(
        environment=environment,
        user_home=tmp_path,
    )

    assert propagated[runtime_paths.HX_HOME_ENV] == str(helix_root)
    assert propagated[runtime_paths.HELIX_STORAGE_ROOT_ENV] == str(helix_root)


def test_existing_config_only_needs_to_be_readable(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config" / "config.toml"
    ConfigStore(config_path).ensure()

    def reject_write(_self, _text) -> None:
        raise AssertionError("existing config must not be rewritten")

    monkeypatch.setattr(ConfigStore, "_write_text", reject_write)

    assert runtime_paths.ensure_config_readable(config_path) == config_path


def test_missing_config_is_created_for_first_start(tmp_path) -> None:
    config_path = tmp_path / "config" / "config.toml"

    assert runtime_paths.ensure_config_readable(config_path) == config_path
    assert config_path.is_file()


def test_config_validation_identifies_config_path(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.mkdir()

    with pytest.raises(AppError, match=r"Config path is not readable:") as raised:
        runtime_paths.ensure_config_readable(config_path)

    assert str(config_path) in str(raised.value)
    assert "State home" not in str(raised.value)


def test_state_validation_creates_runtime_directories_and_cleans_probe(
    tmp_path,
) -> None:
    state_root = tmp_path / "state"

    assert runtime_paths.ensure_state_home(state_root) == state_root
    assert {path.name for path in state_root.iterdir()} == {
        "history",
        "reports",
        "sessions",
    }


def test_state_write_failure_identifies_state_home(tmp_path) -> None:
    state_root = tmp_path / "state"
    state_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(AppError, match=r"State home is not writable:") as raised:
        runtime_paths.ensure_state_home(state_root)

    assert str(state_root) in str(raised.value)
    assert "Config path" not in str(raised.value)


def test_state_lock_failure_is_not_reported_as_write_failure(
    monkeypatch,
    tmp_path,
) -> None:
    state_root = tmp_path / "state"

    def reject_lock(_path) -> None:
        raise sqlite3.OperationalError("locking is unavailable")

    monkeypatch.setattr(runtime_paths, "_ensure_sqlite_lockable", reject_lock)

    with pytest.raises(AppError, match=r"State home is not lockable:") as raised:
        runtime_paths.ensure_state_home(state_root)

    assert str(state_root) in str(raised.value)
    assert "not writable" not in str(raised.value)


_CROSS_PROCESS_SQLITE = """
import sqlite3
import sys

from infrastructure.config.runtime_paths import (
    agent_runtime_db_path,
    ensure_state_home,
)

ensure_state_home()
connection = sqlite3.connect(agent_runtime_db_path())
connection.execute(
    "CREATE TABLE IF NOT EXISTS state_probe (value TEXT NOT NULL UNIQUE)"
)
if sys.argv[1] == "write":
    connection.execute(
        "INSERT OR IGNORE INTO state_probe (value) VALUES (?)",
        ("persisted",),
    )
    connection.commit()
count = connection.execute("SELECT COUNT(*) FROM state_probe").fetchone()[0]
connection.close()
print(count)
"""


def _run_state_probe(
    workspace: Path,
    environment: dict[str, str],
    operation: str,
) -> subprocess.CompletedProcess[str]:
    """在独立进程中读写状态根对应的 SQLite。"""
    return subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS_SQLITE, operation],
        cwd=workspace,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_state_home_is_reused_across_processes_and_isolated_between_cases(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    workspace = repository_root
    config_root = tmp_path / "config"
    first_state_root = tmp_path / "case-001"
    second_state_root = tmp_path / "case-002"
    base_environment = dict(os.environ)
    base_environment.pop(runtime_paths.HX_HOME_ENV, None)
    base_environment[CONFIG_HOME_ENV] = str(config_root)

    first_environment = {
        **base_environment,
        STATE_HOME_ENV: str(first_state_root),
    }
    second_environment = {
        **base_environment,
        STATE_HOME_ENV: str(second_state_root),
    }

    written = _run_state_probe(workspace, first_environment, "write")
    resumed = _run_state_probe(workspace, first_environment, "read")
    isolated = _run_state_probe(workspace, second_environment, "read")

    assert written.stdout.strip() == "1"
    assert resumed.stdout.strip() == "1"
    assert isolated.stdout.strip() == "0"
    assert (first_state_root / "history" / "runtime.db").is_file()
    assert (second_state_root / "history" / "runtime.db").is_file()
    assert not (config_root / "history").exists()
