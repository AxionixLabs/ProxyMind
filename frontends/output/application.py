# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import os
import shutil
import sys
import typing

from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    StyledBlock,
    TextStyle,
    Viewport,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from frontends.terminal.text import sanitize_styled_block
from metadata import const

ANSI_RESET = "\x1b[0m"

_ANSI_FOREGROUND_CODES = {
    "ansiblack": 30,
    "ansired": 31,
    "ansigreen": 32,
    "ansiyellow": 33,
    "ansiblue": 34,
    "ansimagenta": 35,
    "ansicyan": 36,
    "ansiwhite": 37,
    "default": 39,
}


def _stream(value: object | None, fallback: typing.TextIO) -> typing.TextIO:
    """从兼容的终端对象中提取纯文本流。"""
    if value is None:
        return fallback
    candidate = getattr(value, "file", value)
    return candidate if callable(getattr(candidate, "write", None)) else fallback


def _supports_color(stream: typing.TextIO) -> bool:
    """判断应用级输出是否可以使用 ANSI 样式。"""
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR") not in {None, "", "0"}:
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


def _ansi_style(style: TextStyle) -> str:
    """把中立样式转换为 ANSI 前缀。"""
    codes: list[str] = []
    if style.bold:
        codes.append("1")
    if style.dim:
        codes.append("2")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if style.reverse:
        codes.append("7")
    if style.strikethrough:
        codes.append("9")
    if style.foreground:
        color = _ansi_color(style.foreground, background=False)
        if color:
            codes.append(color)
    if style.background:
        color = _ansi_color(style.background, background=True)
        if color:
            codes.append(color)
    return f"\x1b[{';'.join(codes)}m" if codes else ""


def _ansi_color(value: str, *, background: bool) -> str | None:
    """把 ANSI 命名色或十六进制颜色转换为控制代码。"""
    text = str(value or "").strip()
    if (code := _ANSI_FOREGROUND_CODES.get(text.casefold())) is not None:
        return str(code + 10 if background else code)
    if len(text) != 7 or not text.startswith("#"):
        return None
    try:
        red, green, blue = (
            int(text[index:index + 2], 16)
            for index in (1, 3, 5)
        )
    except ValueError:
        return None
    channel = 48 if background else 38
    return f"{channel};2;{red};{green};{blue}"


def _write(stream: typing.TextIO, text: str) -> None:
    """写入并刷新应用级文本。"""
    stream.write(text)
    stream.flush()


class ConsoleApplicationSink(ApplicationSink):
    """通过纯文本流输出应用级展示数据。"""

    def __init__(
        self,
        console: object | None = None,
        error_console: object | None = None,
    ) -> None:
        self.console = console or sys.stdout
        self.error_console = error_console or sys.stderr
        self._stream = _stream(self.console, sys.stdout)
        self._error_stream = _stream(self.error_console, sys.stderr)

    @property
    def viewport(self) -> Viewport:
        """返回当前终端尺寸。"""
        size = shutil.get_terminal_size(fallback=(80, 24))
        return Viewport(width=size.columns, height=size.lines)

    def emit(self, view: ApplicationView) -> None:
        """输出一项应用级终端展示。"""
        stream = (
            self._error_stream
            if view.payload.get("stream") == "stderr"
            else self._stream
        )
        color = _supports_color(stream)

        if view.type in {"intro", "outro", "startup_logo", "spacer"}:
            return None
        if view.type == "error":
            head = f"{const.APP_DESC} ::"
            label = "ERROR"
            if color:
                head_style = _ansi_style(semantic_text_style(
                    TerminalSemanticRole.SECONDARY,
                    bold=True,
                ))
                label_style = _ansi_style(semantic_text_style(
                    TerminalSemanticRole.FAILURE,
                    bold=True,
                ))
                head = f"{head_style}{head}{ANSI_RESET}"
                label = f"{label_style} {label} {ANSI_RESET}"
            _write(stream, f"{head} {label}: {view.renderable}\n")
            return None
        if view.type == "json" and isinstance(view.renderable, dict):
            _write(stream, json.dumps(
                view.renderable,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ) + "\n")
            return None
        if isinstance(view.renderable, StyledBlock):
            block = sanitize_styled_block(view.renderable)
            parts: list[str] = []
            for span in block.spans or ():
                prefix = _ansi_style(span.style) if color else ""
                suffix = ANSI_RESET if prefix else ""
                parts.append(f"{prefix}{span.text}{suffix}")
            _write(stream, "".join(parts) or block.plain_text)
            if view.end:
                _write(stream, view.end)
            return None
        _write(stream, f"{view.renderable or ''}{view.end}")


class JsonApplicationSink(ApplicationSink):
    """只写出入口级 JSONL 事件。"""

    def __init__(self, stream: typing.TextIO) -> None:
        self.stream = stream

    @property
    def viewport(self) -> Viewport:
        """返回空展示尺寸。"""
        return Viewport()

    def _write(self, payload: dict[str, typing.Any]) -> None:
        """写出单个入口级 JSONL 事件。"""
        line = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        self.stream.write(line + "\n")
        self.stream.flush()

    def emit(self, view: ApplicationView) -> None:
        """写出 JSON 事件并忽略其他应用展示。"""
        if view.type == "config.warning":
            for warning in view.payload.get("warnings", ()):
                self._write({
                    "type": "config.warning",
                    "message": str(warning),
                })
            return None
        if view.type != "json" or not isinstance(view.renderable, dict):
            return None
        self._write(view.renderable)


class NullApplicationSink(ApplicationSink):
    """忽略无终端前端的应用级展示。"""

    @property
    def viewport(self) -> Viewport:
        """返回空展示尺寸。"""
        return Viewport()

    def emit(self, view: ApplicationView) -> None:
        """忽略应用级展示数据。"""
        _ = view
        return None


if __name__ == '__main__':
    pass
