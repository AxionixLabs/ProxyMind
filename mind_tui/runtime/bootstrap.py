# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Any

from engine.tinker import Active, MindError
from mind_app.mind_core import Mind
from mind_app.paths import ensure_mind_home, mind_config_path, mind_reports_dir
from mind_app.runtime.environment.exec_env import clear_exec_env_cache
from mind_app.runtime.environment.shell_tools import route_shell_tools
from mind_core.preference import Preferences
from mind_core.service_config import ServiceConfig
from mind_nova import const
from mind_nova.services import service_endpoints


@asynccontextmanager
async def open_runtime() -> AsyncIterator[Mind]:
    """初始化独立 TUI 使用的应用上下文并在退出时清理。"""
    level = "INFO"
    Active.active(level)
    route_shell_tools(_supports_root())
    clear_exec_env_cache()

    home = ensure_mind_home()
    preferences = Preferences(str(mind_config_path()))
    await preferences.load_pref()
    service_endpoints.configure(await ServiceConfig().load_domain())

    positions = (None, None, None, False, False, None)
    runtime = Mind(
        [],
        level,
        os.cpu_count() or 1,
        {},
        *positions,
        src_opera_place=str(home),
        src_total_place=str(mind_reports_dir()),
        pref=preferences,
        workspace_root=Path.cwd()
    )
    runtime.bind_runtime(asyncio.get_running_loop(), asyncio.current_task())

    try:
        await runtime.start_external_mcp_runtime()
        yield runtime
    finally:
        await runtime.await_cleanup(runtime.close_runtime_resources())


def runtime_labels(
    runtime: Mind,
    pref_config: dict[str, Any]
) -> tuple[str, str]:
    """返回底部信息栏使用的模型和工作区标签。"""
    primary = (
        pref_config.get("primary")
        if isinstance(pref_config.get("primary"), dict)
        else {}
    )
    model = str(primary.get("model") or "model").strip()
    effort = str(primary.get("reasoning_effort") or "").strip()
    model_label = " ".join(part for part in (model, effort) if part)
    return model_label, str(runtime.history_workspace)


def _supports_root() -> Path:
    """返回当前平台使用的内置命令行工具目录。"""
    platform_name = sys.platform.strip().lower()
    if platform_name == "win32":
        folder = "windows"
    elif platform_name == "darwin":
        folder = "macos"
    else:
        raise MindError(
            f"{const.APP_DESC} is not supported on this platform: {platform_name}."
        )
    project_root = Path(__file__).resolve().parents[2]
    return project_root / const.SCHEMATIC / const.SUPPORTS / folder
