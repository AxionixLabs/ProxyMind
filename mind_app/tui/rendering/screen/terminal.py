# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from mind_core.design.terminal_capabilities import (
    TerminalCapabilities,
    TerminalKind
)


def queued_message_edit_binding(capabilities: TerminalCapabilities) -> str:
    """返回当前终端适合展示的队尾编辑按键。"""
    identity = capabilities.identity
    if (
        identity.multiplexer == TerminalKind.TMUX
        or identity.kind in {
            TerminalKind.APPLE_TERMINAL,
            TerminalKind.ITERM2,
            TerminalKind.VSCODE,
            TerminalKind.WARP,
        }
    ):
        return "shift + ←"
    return "alt + ↑"


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


if __name__ == '__main__':
    pass
