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
from .context import normalize_reasoning_effort, save_primary_pref_field
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    fragment_block,
    text_block,
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
        renderable=text_block(
            f"{pref_command} invalid: /{pref_command}",
            FAILURE_STYLE,
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
        saved = await save_primary_pref_field(field_name, field_value)
        await mind.refresh_pref_if_stale(ttl_sec=0.0)
    except (OSError, TypeError, ValueError) as pref_save_error:
        application.emit(ApplicationView(
            type="tui.command",
            renderable=text_block(
                f"{command_name} save failed: "
                f"{type(pref_save_error).__name__}: {pref_save_error}",
                FAILURE_STYLE,
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    saved_primary = saved.get("primary") if isinstance(saved, dict) else {}
    return saved_primary if isinstance(saved_primary, dict) else {}


async def choose_model_effort(
    runtime: "TuiRuntime",
    current_effort: typing.Any,
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


def render_model_effort_status(
    application: ApplicationSink,
    effort: typing.Any,
) -> None:
    """展示当前模型推理强度。"""
    normalized = normalize_reasoning_effort(effort)
    application.emit(ApplicationView(
        type="tui.model_effort",
        renderable=fragment_block(
            TextSpan("Reasoning Effort ", ACCENT_STYLE),
            TextSpan(f"· {normalized}", BRIGHT_STYLE),
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
