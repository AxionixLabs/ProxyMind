# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
from dataclasses import dataclass


class ToolDisplayKind(enum.Enum):
    """描述工具结果在共享展示层中的内容类型。"""

    GENERIC = "generic"
    SHELL = "shell"
    STDIN = "stdin"
    JAVASCRIPT = "javascript"
    JAVASCRIPT_RESET = "javascript_reset"
    PATCH = "patch"


@dataclass(frozen=True, slots=True)
class ToolDisplaySpec:
    """描述工具展示和历史记录所需的稳定策略。"""

    kind: ToolDisplayKind
    two_stage: bool = False
    source_field: str | None = None


_GENERIC_SPEC = ToolDisplaySpec(
    ToolDisplayKind.GENERIC,
    two_stage=True,
)

_TOOL_DISPLAY_SPECS = {
    "shell_command": ToolDisplaySpec(
        ToolDisplayKind.SHELL,
        two_stage=True,
    ),
    "exec_command": ToolDisplaySpec(ToolDisplayKind.SHELL),
    "read_repository": ToolDisplaySpec(
        ToolDisplayKind.SHELL,
        two_stage=True,
    ),
    "write_stdin": ToolDisplaySpec(ToolDisplayKind.STDIN),
    "apply_patch": ToolDisplaySpec(
        ToolDisplayKind.PATCH,
        two_stage=True,
    ),
    "js_repl": ToolDisplaySpec(
        ToolDisplayKind.JAVASCRIPT,
        two_stage=True,
        source_field="code",
    ),
    "js_repl_reset": ToolDisplaySpec(ToolDisplayKind.JAVASCRIPT_RESET),
    "view_image": ToolDisplaySpec(ToolDisplayKind.GENERIC),
}

NATIVE_TOOL_NAMES = frozenset(
    name
    for name, spec in _TOOL_DISPLAY_SPECS.items()
    if spec.kind is not ToolDisplayKind.GENERIC
)


def tool_display_spec(name: str) -> ToolDisplaySpec:
    """返回工具在展示、历史和交互层共用的策略。"""

    normalized = str(name or "").strip()
    return _TOOL_DISPLAY_SPECS.get(normalized, _GENERIC_SPEC)


def is_two_stage_tool(name: str) -> bool:
    """判断工具是否需要分别记录开始和完成阶段。"""

    return tool_display_spec(name).two_stage


def uses_native_tool_view(name: str) -> bool:
    """判断工具是否使用结构化原生工具视图。"""

    return tool_display_spec(name).kind is not ToolDisplayKind.GENERIC


if __name__ == '__main__':
    pass
