# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from mind_app.presentation.models import TextSpan
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import (
    MenuOption,
    MenuRequest
)
from mind_core.provider_config import DEFAULT_REASONING_EFFORT
from mind_core.config import config_to_preferences
from mind_core.config_session import ConfigSession
from .context import (
    normalize_reasoning_effort,
    save_primary_pref_field
)
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    command_result_block,
    failure_text_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime


MODEL_EFFORT_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("low", "Low", "低推理，优先速度"),
    ("medium", "Medium", "默认档位，平衡速度与质量"),
    ("high", "High", "高推理，提升复杂任务质量"),
    ("xhigh", "Extra high", "最高推理，适合困难任务"),
)


async def exchange_pref_value(
    application: ApplicationSink,
    matcher: re.Match[str],
    pref_command: typing.Literal["model", "apikey", "base-url"],
) -> typing.Optional[str]:
    """解析模型偏好类指令，并给出交互提示。"""
    if pref_command == "model":
        return matcher.group(1).strip() if matcher.group(1) else ""

    if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
        return pref_name

    help_by_command = {
        "model": "<model> (Model name or ID)",
        "apikey": "<apikey> (Provider API key)",
        "base-url": "<url> (Provider base URL)",
    }
    application.emit(ApplicationView(
        type="tui.preference.help",
        renderable=text_block(
            f"  • {help_by_command[pref_command]}",
            ACCENT_STYLE,
        ),
    ))
    application.emit(ApplicationView(
        type="tui.preference.invalid",
        renderable=failure_text_block(
            f"{pref_command} invalid: /{pref_command}",
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))
    return None


async def persist_primary_pref(
    mind: "Mind",
    *,
    command_name: typing.Literal[
        "model", "apikey", "base-url", "model-effort"
    ],
    field_name: typing.Literal[
        "model", "apikey", "base_url", "reasoning_effort"
    ],
    field_value: str,
) -> typing.Optional[dict[str, typing.Any]]:
    """把 TUI 偏好命令写入 primary slot，并刷新本地缓存。"""
    application = mind.frontend.application

    try:
        saved = await save_primary_pref_field(
            mind.config_session,
            field_name,
            field_value,
        )
        await mind.refresh_pref_if_stale(ttl_sec=0.0)
    except (OSError, TypeError, ValueError) as pref_save_error:
        command = "/effort" if command_name == "model-effort" else f"/{command_name}"
        application.emit(ApplicationView(
            type="tui.command",
            renderable=command_result_block(
                command,
                TextSpan(
                    f"Failed: {type(pref_save_error).__name__}: "
                    f"{pref_save_error}",
                    FAILURE_STYLE,
                ),
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    saved_primary = saved.get("primary") if isinstance(saved, dict) else {}
    return saved_primary if isinstance(saved_primary, dict) else {}


async def choose_model_effort(
    runtime: "TuiRuntime",
    current_effort: typing.Any
) -> str | None:
    """在主 TUI 中选择模型推理强度。"""
    current = normalize_reasoning_effort(current_effort)
    return await runtime.select_menu(MenuRequest(
        title="Reasoning Effort",
        status=f"current={current}",
        options=tuple(
            MenuOption(value=value, label=label, detail=detail)
            for value, label, detail in MODEL_EFFORT_OPTIONS
        ),
        selected=_default_effort_index(current),
    ))


async def choose_provider(
    runtime: "TuiRuntime",
    session: ConfigSession
) -> str | None:
    """从当前配置中选择一个 Provider Profile。"""
    raw       = session.store.read_raw()
    providers = raw.get("model_providers") if isinstance(raw, dict) else {}
    profiles  = providers if isinstance(providers, dict) else {}

    if not profiles:
        return None

    primary  = config_to_preferences(session.load()).get("primary") or {}
    active   = str(primary.get("provider") or "")
    ids      = [key for key, value in profiles.items() if isinstance(value, dict)]
    selected = ids.index(active) if active in ids else 0

    return await runtime.select_menu(MenuRequest(
        title="Provider",
        status=f"current={active or '(none)'}",
        help_text="Up/Down select · Enter use · Esc/q cancel",
        options=tuple(
            MenuOption(
                value=profile_id,
                label=str(profile.get("name") or profile_id),
                detail=_provider_detail(profile),
            )
            for profile_id, profile in profiles.items()
            if isinstance(profile, dict)
        ),
        selected=selected,
    ))


async def save_active_provider(
    session: ConfigSession,
    provider_id: str
) -> dict[str, typing.Any]:
    """持久化当前 Provider Profile 并返回运行时偏好。"""
    normalized = str(provider_id or "").strip()
    raw        = session.store.read_raw()
    providers  = raw.get("model_providers") if isinstance(raw, dict) else {}
    profile    = providers.get(normalized) if isinstance(providers, dict) else None

    if not isinstance(profile, dict):
        raise ValueError(f"provider does not exist: {normalized}")
    if not str(profile.get("model") or "").strip():
        raise ValueError(f"provider is incomplete: {normalized}")

    config = session.update_user({("model_provider",): normalized})
    return config_to_preferences(config)


def _provider_detail(profile: dict[str, typing.Any]) -> str:
    """生成 Provider 菜单项的单行摘要。"""
    kind  = str(profile.get("kind") or "unknown")
    model = str(profile.get("model") or "(incomplete)")
    route = str(profile.get("route") or "")
    return " · ".join(value for value in (kind, model, route) if value)


def render_model_effort_status(
    application: ApplicationSink,
    effort: typing.Any
) -> None:
    """展示当前模型推理强度。"""
    normalized = normalize_reasoning_effort(effort)
    application.emit(ApplicationView(
        type="tui.model_effort",
        renderable=command_result_block(
            "/effort",
            TextSpan(normalized, BRIGHT_STYLE),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


def _default_effort_index(current_effort: str) -> int:
    """返回当前推理强度对应的菜单位置。"""
    for index, (value, _label, _detail) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == current_effort:
            return index
    for index, (value, _label, _detail) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == DEFAULT_REASONING_EFFORT:
            return index

    return 0


if __name__ == '__main__':
    pass
