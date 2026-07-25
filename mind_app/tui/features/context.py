# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import (
    Path,
    PurePath
)
from prompt_toolkit.utils import get_cwidth
from mind_core.config import (
    config_to_preferences,
    ModelConfigField,
    model_config_field_values
)
from mind_core.config_session import ConfigSession
from mind_core.provider_config import (
    DEFAULT_REASONING_EFFORT,
    SUPPORTED_REASONING_EFFORTS
)

WORKSPACE_LABEL_REFRESH: float = 5.0
WORKSPACE_LABEL_UNKNOWN: str   = "?"
EXEC_STATUS_COMMAND_MIN: int   = 12
EXEC_STATUS_COMMAND_MAX: int   = 56


def ignored_tui_input(raw: str) -> bool:
    """判断 TUI 输入是否应仅换行并跳过请求链路。"""
    stripped = str(raw or "").strip()
    if not stripped:
        return True
    return stripped in {"$", "/", "\\"}


def workspace_display_label(
    runtime_root: typing.Optional[PurePath],
    *,
    home: typing.Optional[PurePath] = None
) -> str:
    """返回运行时 workspace 的 prompt 展示名称。"""
    if runtime_root is None:
        return WORKSPACE_LABEL_UNKNOWN

    try:
        resolved_root = normalize_display_path(runtime_root)
        home_root = normalize_display_path(home or Path.home())
    except (OSError, RuntimeError, ValueError):
        return WORKSPACE_LABEL_UNKNOWN

    if resolved_root == home_root:
        return "~"

    try:
        relative = resolved_root.relative_to(home_root)
    except ValueError:
        return str(resolved_root)

    relative_text = str(relative)
    if not relative_text:
        return "~"
    separator = "\\" if "\\" in relative_text else "/"
    return f"~{separator}{relative_text}"


def normalize_display_path(
    path: PurePath
) -> PurePath:
    """规范化真实路径；纯路径对象保持原平台风格。"""
    if isinstance(path, Path):
        return path.expanduser().resolve()
    return path


def primary_model_from_config(
    pref_config: dict[str, typing.Any],
    fallback: str = ""
) -> str:
    """从偏好配置中读取主模型名称。"""
    primary = pref_config.get("primary") or {}
    if "model" not in primary:
        return fallback
    return str(primary.get("model") or "")


def primary_model_prompt_label(
    pref_config: dict[str, typing.Any],
    fallback: str = ""
) -> str:
    """生成 prompt 中展示的主模型标签。"""
    model = primary_model_from_config(pref_config, fallback).strip()
    if not model:
        return model

    primary = pref_config.get("primary") or {}

    effort = str(primary.get("reasoning_effort") or "").strip()
    if not effort:
        return model

    return f"{model} {effort}"


def normalize_reasoning_effort(value: typing.Any) -> str:
    """规范化推理强度展示和写入值。"""
    text = str(value or "").strip().lower()
    if text in SUPPORTED_REASONING_EFFORTS:
        return text
    return DEFAULT_REASONING_EFFORT


def exec_status_display_label(
    snapshot: typing.Any,
    *,
    command_limit: int | None = None,
    line_width: int | None = None
) -> str:
    """生成后台进程状态行使用的 exec 会话摘要。"""
    if not isinstance(snapshot, dict):
        return ""

    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return ""

    items = [item for item in raw_items if isinstance(item, dict)]
    if not items:
        return ""

    count  = snapshot.get("count")
    total  = int(count) if isinstance(count, int) and count >= len(items) else len(items)
    extra  = max(0, total - 1)
    suffix = f" · +{extra}" if extra else ""

    limit = _exec_status_command_limit(
        command_limit=command_limit,
        line_width=line_width,
        suffix=suffix
    )
    command = _clip_exec_status_command(items[0].get("command"), limit=limit)
    if not command:
        return ""

    if extra:
        return f"{command}{suffix}"
    return command


def _exec_status_command_limit(
    *,
    command_limit: int | None,
    line_width: int | None,
    suffix: str
) -> int:
    """计算 exec 状态命令的展示宽度上限。"""
    if command_limit is not None:
        return max(1, int(command_limit))

    try:
        width = int(line_width) if line_width is not None else 80
    except (TypeError, ValueError):
        width = 80

    available = width - 7 - get_cwidth(suffix)

    if available < EXEC_STATUS_COMMAND_MIN:
        return max(1, available)

    return min(EXEC_STATUS_COMMAND_MAX, available)


def _clip_exec_status_command(value: typing.Any, *, limit: int) -> str:
    """裁剪 exec 状态中的命令文本。"""
    text = " ".join(str(value or "").split())
    if not text:
        return ""

    size = max(1, int(limit or 1))
    if get_cwidth(text) <= size:
        return text
    if size <= 1:
        return "…"

    available = size - get_cwidth("…")
    used      = 0

    chars: list[str] = []
    for char in text:
        char_width = max(0, get_cwidth(char))
        if used + char_width > available:
            break
        chars.append(char)
        used += char_width
    return f"{''.join(chars)}…"


async def save_primary_pref_field(
    session: ConfigSession,
    field: ModelConfigField,
    value: str
) -> dict[str, typing.Any]:
    """更新 primary 模型槽位的单个字段并持久化到本地配置。"""
    normalized = str(value or "").strip()
    if field != "model" and not normalized:
        raise ValueError(f"{field} is empty")

    if field == "reasoning_effort":
        normalized = normalize_reasoning_effort(normalized)

    preferences = config_to_preferences(session.load())

    primary = dict(preferences.get("primary") or {})
    primary[field] = normalized

    config = session.update(model_config_field_values(primary, field))

    return config_to_preferences(config)


if __name__ == '__main__':
    pass
