# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import httpx
import typing
import asyncio
from pathlib import Path
from mind_core.prompting.box import PromptHeaderState
from mind_nova import const

if typing.TYPE_CHECKING:
    from ...mind_core import Mind

WORKSPACE_STATUS_REFRESH_TTL_SEC: float = 5.0


def workspace_match_status(
    mind_root: Path,
    runtime_root: typing.Optional[Path]
) -> str:
    """比较 Mind 与运行时 workspace，并返回输入框状态符号。"""
    if runtime_root is None:
        return "?"
    return "=" if runtime_root == mind_root else "!"


def primary_model_from_config(
    pref_config: dict[str, typing.Any],
    fallback: str = ""
) -> str:
    """从偏好配置中读取主模型名称。"""
    primary = pref_config.get("primary") or {}
    return primary.get("model", "") or fallback


async def fetch_runtime_workspace_root(
    timeout: float = 0.8
) -> typing.Optional[Path]:
    """读取本地运行时当前 workspace 根目录。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(f"{const.BASE_URL}/api/runtime/exec-env")
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None

    data      = body.get("data") if isinstance(body, dict) else None
    workspace = data.get("workspace") if isinstance(data, dict) else None

    root = workspace.get("root") if isinstance(workspace, dict) else None
    if not isinstance(root, str) or not root.strip():
        return None

    try:
        return Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


async def refresh_prompt_header_state(
    mind: "Mind",
    state: PromptHeaderState,
    *,
    mind_workspace_root: Path,
    interval_sec: float = 2.0
) -> None:
    """等待输入时异步刷新 prompt 头部状态。"""
    while not mind.task_event.is_set():

        pref_config            = await mind.fresh_pref_config()
        runtime_workspace_root = await fetch_runtime_workspace_root()

        state.update(
            model=primary_model_from_config(pref_config, state.model),
            workspace_status=workspace_match_status(
                mind_workspace_root,
                runtime_workspace_root
            )
        )
        await asyncio.sleep(interval_sec)


async def stop_prompt_header_refresh(
    task: typing.Optional[asyncio.Task[None]]
) -> None:
    """停止当前输入框头部刷新任务。"""
    if task is None:
        return None
    if task.done():
        try:
            task.result()
        except asyncio.CancelledError:
            return None
        except RuntimeError:
            return None
        return None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return None


if __name__ == '__main__':
    pass
