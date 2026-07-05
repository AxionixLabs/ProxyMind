# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import (
    Path, PurePath
)
from mind_app.paths import mind_config_path
from mind_core.config import (
    config_to_preferences,
    ensure_config,
    load_config,
    write_config
)
from mind_app.runtime.environment.exec_env import exec_env

WORKSPACE_LABEL_REFRESH: float = 5.0
WORKSPACE_LABEL_UNKNOWN: str   = "?"


def ignored_repl_input(raw: str) -> bool:
    """判断 REPL 输入是否应仅换行并跳过请求链路。"""
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


async def fetch_runtime_workspace_root(
    timeout: float = 0.8
) -> typing.Optional[Path]:
    """读取本地 workspace 根目录。"""
    _ = timeout
    data = exec_env()
    workspace = data.get("workspace") if isinstance(data, dict) else None

    root = workspace.get("root") if isinstance(workspace, dict) else None
    if not isinstance(root, str) or not root.strip():
        root = str(Path.cwd())

    try:
        return Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


async def save_primary_pref_field(
    field: typing.Literal["model", "apikey", "base_url"],
    value: str
) -> dict[str, typing.Any]:
    """更新 primary 模型槽位的单个字段并持久化到本地配置。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is empty")

    target       = ensure_config(mind_config_path())
    config       = load_config(target)
    model_config = config.setdefault("model", {})

    primary = dict(model_config.get("primary") or {})
    primary[field] = normalized

    model_config["primary"] = primary

    written = write_config(target, config)
    return config_to_preferences(written)


if __name__ == '__main__':
    pass
