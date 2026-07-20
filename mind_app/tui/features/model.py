# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

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
from .context import normalize_reasoning_effort
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


MODEL_EFFORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("low", "低推理，优先速度"),
    ("medium", "默认档位，平衡速度与质量"),
    ("high", "高推理，提升复杂任务质量"),
    ("xhigh", "最高推理，适合困难任务"),
)


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
            MenuOption(value=value, label=value, detail=detail)
            for value, detail in MODEL_EFFORT_OPTIONS
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
    for index, (value, _) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == current_effort:
            return index
    for index, (value, _) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == DEFAULT_REASONING_EFFORT:
            return index
    return 0


if __name__ == '__main__':
    pass
