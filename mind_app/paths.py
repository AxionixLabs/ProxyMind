# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import contextlib
from pathlib import Path
from engine.tinker import MindError
from mind_nova import const

MD_HOME_ENV = "MIND_HOME"
HX_HOME_ENV = "HELIX_HOME"


def mind_home() -> Path:
    """返回 Mind 用户级统一目录。"""
    return Path(os.environ.get(MD_HOME_ENV) or Path.home() / ".mind").expanduser()


def helix_home() -> Path:
    """返回 Helix 在 Mind 统一目录下的数据根。"""
    return Path(os.environ.get(HX_HOME_ENV) or mind_home() / "helix").expanduser()


def mind_mcp_servers_path() -> Path:
    """返回外部 MCP 配置文件路径。"""
    return mind_home() / "mcp_servers.json"


def mind_pref_path() -> Path:
    """返回 Mind 本地偏好缓存文件路径。"""
    return mind_home() / "mind_pref.json"


def mind_reports_dir() -> Path:
    """返回 Mind 报告输出目录。"""
    return mind_home() / "reports"


def mind_history_dir() -> Path:
    """返回 Mind 对话历史本地目录。"""
    return mind_home() / "history"


def mind_history_db_path() -> Path:
    """返回 Mind 对话历史 SQLite 文件路径。"""
    return mind_history_dir() / "history.db"


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


def ensure_writable_file(path: Path) -> Path:
    """确保文件所在目录和文件本身可写。"""
    target = Path(path).expanduser()
    ensure_writable_dir(target.parent)

    with target.open("a+b"):
        pass
    return target


def ensure_mcp_servers_file() -> Path:
    """确保 MCP 配置文件存在且非空。"""
    default_mcp_servers = '{\n  "mcpServers": {}\n}\n'

    target = ensure_writable_file(mind_mcp_servers_path())
    if target.stat().st_size <= 0:
        target.write_text(default_mcp_servers, encoding=const.CHARSET)

    return target


def ensure_mind_home() -> Path:
    """确保 Mind 用户级统一目录及常用子路径可写。"""
    try:
        root = ensure_writable_dir(mind_home())
        ensure_writable_dir(mind_reports_dir())
        ensure_writable_dir(mind_history_dir())
        ensure_mcp_servers_file()
        return root
    except OSError as exc:
        raise MindError(
            f"Home is not writable: {mind_home()} ({type(exc).__name__}: {exc})"
        ) from exc


def process_env() -> dict[str, str]:
    """返回传递给子进程的统一目录环境变量。"""
    root = mind_home()
    hx   = helix_home()

    return {
        MD_HOME_ENV          : str(root),
        HX_HOME_ENV          : str(hx),
        "HELIX_STORAGE_ROOT" : str(hx)
    }


if __name__ == "__main__":
    pass
