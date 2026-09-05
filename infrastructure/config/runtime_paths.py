# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import os
import sqlite3
import typing
from pathlib import Path

from infrastructure.config.paths import (
    CONFIG_HOME_ENV,
    STATE_HOME_ENV,
    default_config_home,
    default_state_home,
)
from infrastructure.config.store import (
    ConfigStore,
    ConfigStoreError,
    default_config_path,
)
from infrastructure.errors import AppError
from metadata import const

HX_HOME_ENV = "HELIX_HOME"
HELIX_STORAGE_ROOT_ENV = "HELIX_STORAGE_ROOT"


def config_home(
    *,
    environment: typing.Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> Path:
    """返回用户配置根目录。"""
    return default_config_home(environment=environment, user_home=user_home)


def state_home(
    *,
    environment: typing.Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> Path:
    """返回可写运行状态根目录。"""
    return default_state_home(environment=environment, user_home=user_home)


def helix_home(
    *,
    environment: typing.Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> Path:
    """返回 Helix 数据根，显式覆盖优先于状态根默认值。"""
    source = os.environ if environment is None else environment
    return Path(
        source.get(HX_HOME_ENV)
        or state_home(environment=source, user_home=user_home) / "helix"
    ).expanduser()


def application_config_path() -> Path:
    """返回应用主配置文件路径。"""
    return default_config_path()


def reports_dir() -> Path:
    """返回应用报告输出目录。"""
    return state_home() / "reports"


def sessions_dir() -> Path:
    """返回本地会话记录目录。"""
    return state_home() / "sessions"


def history_dir() -> Path:
    """返回对话历史本地目录。"""
    return state_home() / "history"


def conversation_history_db_path() -> Path:
    """返回对话历史 SQLite 文件路径。"""
    return history_dir() / "history.db"


def agent_graph_db_path() -> Path:
    """返回执行树 SQLite 文件路径。"""
    return history_dir() / "agents.db"


def effect_journal_db_path() -> Path:
    """返回本地效果账本 SQLite 文件路径。"""
    return history_dir() / "effects.db"


def approval_fact_db_path() -> Path:
    """返回审批事实 SQLite 文件路径。"""
    return history_dir() / "approvals.db"


def agent_runtime_db_path() -> Path:
    """返回 Agent Harness 事件、快照和 outbox SQLite 文件路径。"""
    return history_dir() / "runtime.db"


def durable_queue_db_path() -> Path:
    """返回 Durable Queue 本地执行快照账本路径。"""
    return history_dir() / "durable_queue.db"


def ensure_writable_dir(path: Path) -> Path:
    """确保目录存在且可写。"""
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    probe = target / f".write_probe_{os.getpid()}_{id(target)}"
    with probe.open("w", encoding=const.CHARSET) as file:
        file.write("")
    with contextlib.suppress(OSError):
        probe.unlink()

    return target


def ensure_config_readable(path: Path | None = None) -> Path:
    """确保主配置文件存在且可读，不要求既有配置根可写。"""
    target = Path(path or application_config_path()).expanduser()

    try:
        ConfigStore(target).ensure()
        if not target.is_file():
            raise OSError("path is not a file")
        with target.open("r", encoding=const.CHARSET):
            pass
    except (ConfigStoreError, OSError) as error:
        raise AppError(
            f"Config path is not readable: {target} "
            f"({type(error).__name__}: {error})"
        ) from error

    return target


def _ensure_sqlite_lockable(path: Path) -> None:
    """通过短生命周期独占事务验证状态根支持 SQLite 文件锁。"""
    probe = path / f".lock_probe_{os.getpid()}_{id(path)}.db"
    connection: sqlite3.Connection | None = None

    try:
        connection = sqlite3.connect(probe, timeout=0.0, isolation_level=None)
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("CREATE TABLE lock_probe (value INTEGER NOT NULL)")
        connection.execute("ROLLBACK")
    finally:
        if connection is not None:
            connection.close()
        for candidate in (
            probe,
            probe.with_name(f"{probe.name}-journal"),
            probe.with_name(f"{probe.name}-shm"),
            probe.with_name(f"{probe.name}-wal"),
        ):
            with contextlib.suppress(OSError):
                candidate.unlink(missing_ok=True)


def ensure_state_home(path: Path | None = None) -> Path:
    """确保运行状态根可创建、可写且支持 SQLite 文件锁。"""
    target = Path(path or state_home()).expanduser()

    try:
        root = ensure_writable_dir(target)

        ensure_writable_dir(root / "reports")
        ensure_writable_dir(root / "sessions")
        ensure_writable_dir(root / "history")

    except OSError as error:
        raise AppError(
            f"State home is not writable: {target} "
            f"({type(error).__name__}: {error})"
        ) from error

    try:
        _ensure_sqlite_lockable(root)
    except (OSError, sqlite3.Error) as error:
        raise AppError(
            f"State home is not lockable: {root} "
            f"({type(error).__name__}: {error})"
        ) from error

    return root


def process_env(
    *,
    environment: typing.Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> dict[str, str]:
    """返回传递给子进程的配置根和运行状态根环境变量。"""
    source = os.environ if environment is None else environment
    config_root = config_home(environment=source, user_home=user_home)
    state_root = state_home(environment=source, user_home=user_home)
    hx = helix_home(environment=source, user_home=user_home)

    return {
        CONFIG_HOME_ENV: str(config_root),
        STATE_HOME_ENV: str(state_root),
        HX_HOME_ENV: str(hx),
        HELIX_STORAGE_ROOT_ENV: str(hx),
    }


if __name__ == '__main__':
    pass
