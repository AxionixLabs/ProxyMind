# -*- coding: utf-8 -*-

import ast
from pathlib import Path

from prompt_toolkit.output import ColorDepth

from frontends.terminal.capabilities import TerminalCapabilities
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from frontends.tui.core.runtime import TuiRuntime

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STYLE_ROOTS = (
    _PROJECT_ROOT / "frontends" / "cli",
    _PROJECT_ROOT / "frontends" / "terminal",
    _PROJECT_ROOT / "frontends" / "tui",
)
_RICH_COLOR_OWNERS = frozenset({
    _PROJECT_ROOT / "frontends" / "terminal" / "highlighting.py",
    _PROJECT_ROOT / "frontends" / "terminal" / "renderers" / "patch.py",
    _PROJECT_ROOT / "frontends" / "terminal" / "semantic_styles.py",
})
_ANSI_COLOR_TOKENS = tuple(
    f"ansi{name}"
    for name in (
        "black",
        "red",
        "green",
        "yellow",
        "blue",
        "magenta",
        "cyan",
        "white",
    )
)


def test_regular_terminal_components_do_not_own_concrete_colors() -> None:
    violations: list[str] = []
    for root in _STYLE_ROOTS:
        for path in root.rglob("*.py"):
            if path in _RICH_COLOR_OWNERS:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                value = node.value.casefold()
                if _contains_hex_color(value) or any(
                    token in value for token in _ANSI_COLOR_TOKENS
                ):
                    relative = path.relative_to(_PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno}: {node.value!r}")

    assert violations == []


def _contains_hex_color(value: str) -> bool:
    """判断字符串是否包含完整六位十六进制颜色。"""

    for index, char in enumerate(value):
        if char != "#" or index + 7 > len(value):
            continue
        candidate = value[index + 1:index + 7]
        if len(candidate) == 6 and all(part in "0123456789abcdef" for part in candidate):
            return True
    return False


def test_no_color_application_style_contains_no_color_values() -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
        color_support=TerminalColorSupport.fixed(TerminalColorLevel.NONE),
    )
    style = TuiRuntime(terminal_capabilities=capabilities).screen.application.style

    assert all(
        "#" not in value and "ansi" not in value
        for _selector, value in style.style_rules
    )


def test_application_output_depth_uses_frozen_terminal_capability() -> None:
    """验证渲染器不会重新从宿主环境推断输出色深。"""
    expectations = (
        (TerminalColorLevel.TRUECOLOR, ColorDepth.DEPTH_24_BIT),
        (TerminalColorLevel.ANSI256, ColorDepth.DEPTH_8_BIT),
        (TerminalColorLevel.ANSI16, ColorDepth.DEPTH_4_BIT),
        (TerminalColorLevel.UNKNOWN, ColorDepth.DEPTH_4_BIT),
        (TerminalColorLevel.NONE, ColorDepth.DEPTH_1_BIT),
    )

    for level, expected in expectations:
        capabilities = TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
            color_support=TerminalColorSupport.fixed(level),
        )
        application = TuiRuntime(
            terminal_capabilities=capabilities
        ).screen.application

        assert application.color_depth is expected


if __name__ == '__main__':
    pass
