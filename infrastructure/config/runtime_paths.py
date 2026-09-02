# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import os
from pathlib import Path

from infrastructure.config.paths import (
    APP_HOME_ENV,
    default_application_home
)
from infrastructure.config.store import default_config_path
from infrastructure.errors import AppError
from metadata import const

HX_HOME_ENV = "HELIX_HOME"


def mind_home() -> Path:
    """返回应用的用户级统一目录。"""
    return default_application_home()


def helix_home() -> Path:
    """返回 Helix 在应用统一目录下的数据根。"""
    return Path(os.environ.get(HX_HOME_ENV) or mind_home() / "helix").expanduser()


def mind_config_path() -> Path:
    """返回应用主配置文件路径。"""
    return default_config_path()


def mind_reports_dir() -> Path:
    """返回应用报告输出目录。"""
    return mind_home() / "reports"


def sessions_dir() -> Path:
    """返回本地会话记录目录。"""
    return mind_home() / "sessions"


def mind_history_dir() -> Path:
    """返回对话历史本地目录。"""
    return mind_home() / "history"


def mind_history_db_path() -> Path:
    """返回对话历史 SQLite 文件路径。"""
    return mind_history_dir() / "history.db"


def agent_graph_db_path() -> Path:
    """返回执行树 SQLite 文件路径。"""
    return mind_history_dir() / "agents.db"


def effect_journal_db_path() -> Path:
    """返回本地效果账本 SQLite 文件路径。"""
    return mind_history_dir() / "effects.db"


def approval_fact_db_path() -> Path:
    """返回审批事实 SQLite 文件路径。"""
    return mind_history_dir() / "approvals.db"


def agent_runtime_db_path() -> Path:
    """返回 Agent Harness 事件、快照和 outbox SQLite 文件路径。"""
    return mind_history_dir() / "runtime.db"


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


def ensure_mind_home() -> Path:
    """确保应用的用户级目录及常用子路径可写。"""
    try:
        root = ensure_writable_dir(mind_home())

        ensure_writable_dir(mind_reports_dir())
        ensure_writable_dir(sessions_dir())
        ensure_writable_dir(mind_history_dir())

        return root

    except OSError as exc:
        raise AppError(
            f"Home is not writable: {mind_home()} ({type(exc).__name__}: {exc})"
        ) from exc


def process_env() -> dict[str, str]:
    """返回传递给子进程的统一目录环境变量。"""
    root = mind_home()
    hx = helix_home()

    return {
        APP_HOME_ENV: str(root),
        HX_HOME_ENV: str(hx),
        "HELIX_STORAGE_ROOT": str(hx)
    }


if __name__ == '__main__':
    pass
