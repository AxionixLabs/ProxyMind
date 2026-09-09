# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys

from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)

_OSC8_TERMINALS = frozenset({
    TerminalKind.VSCODE,
    TerminalKind.VTE,
    TerminalKind.ZELLIJ,
    TerminalKind.GHOSTTY,
    TerminalKind.KITTY,
    TerminalKind.ITERM2,
    TerminalKind.WEZTERM,
    TerminalKind.ALACRITTY,
    TerminalKind.KONSOLE,
    TerminalKind.WINDOWS_TERMINAL,
})


def queued_message_edit_binding(
    identity: TerminalIdentity,
    bindings: tuple[str, ...],
) -> str:
    """从有效绑定中选择适合当前终端展示的队尾编辑按键。"""
    if not bindings:
        return ""
    if (
        identity.multiplexer == TerminalKind.TMUX
        or identity.kind in {
            TerminalKind.APPLE_TERMINAL,
            TerminalKind.VSCODE,
            TerminalKind.WARP,
        }
    ):
        preferred = "Shift+Left"
    else:
        preferred = "Alt+Up"
    selected = next(
        (
            binding
            for binding in bindings
            if binding.casefold() == preferred.casefold()
        ),
        bindings[0],
    )
    display = {
        "alt+up": "alt + ↑",
        "shift+left": "shift + ←",
    }
    return display.get(selected.casefold(), selected.casefold())


def supports_terminal_hyperlinks(identity: TerminalIdentity) -> bool:
    """判断终端身份是否属于已验证的 OSC 8 实现。"""

    return identity.kind in _OSC8_TERMINALS


def supports_vt_control(output: Output) -> bool:
    """判断输出对象是否可以安全接收 VT 控制序列。"""
    if isinstance(output, (DummyOutput, PlainTextOutput)):
        return False
    if (
        sys.platform == "win32"
        and not isinstance(output, Vt100_Output)
        and not hasattr(output, "vt100_output")
    ):
        return False
    return True


def erase_terminal_scrollback(output: Output) -> None:
    """清除支持 VT 擦除指令的终端滚屏缓冲区。"""
    if not supports_vt_control(output):
        return None
    output.write_raw("\x1b[3J")
    output.flush()


def clear_terminal_for_resize_replay(output: Output) -> None:
    """为尺寸重排清除可见画面和原生滚屏缓冲区。"""
    if not supports_vt_control(output):
        return None
    output.write_raw("\x1b[r\x1b[0m\x1b[H\x1b[2J\x1b[3J\x1b[H")


def set_synchronized_output(output: Output, active: bool) -> bool:
    """切换支持终端的同步输出更新区间。"""
    if not supports_vt_control(output):
        return False
    output.write_raw("\x1b[?2026h" if active else "\x1b[?2026l")
    output.flush()
    return True


def set_alternate_scroll_mode(output: Output, active: bool) -> bool:
    """切换 alternate screen 中的滚轮方向键转换。"""
    if not supports_vt_control(output):
        return False
    output.write_raw("\x1b[?1007h" if active else "\x1b[?1007l")
    return True


def scroll_inline_viewport(output: Output, lines: int) -> None:
    """按指定行数向上滚动当前 VT 物理视口。"""
    if lines <= 0:
        return None
    if not supports_vt_control(output):
        raise RuntimeError("inline viewport scrolling requires VT output")
    output.write_raw(f"\x1b[{lines}S")


if __name__ == '__main__':
    pass
