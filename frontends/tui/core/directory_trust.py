# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from pathlib import Path
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal_text import sanitize_terminal_text
from ..rendering.fragments import (
    fragments_text,
    iter_text_units
)

DirectoryTrustChoice: typing.TypeAlias = typing.Literal["trust", "quit"]


@dataclass
class DirectoryTrustState(object):
    """保存目录信任界面的路径、选择和等待结果。"""
    cwd: str
    trust_target: str
    highlighted: DirectoryTrustChoice
    future: asyncio.Future[DirectoryTrustChoice]
    error: str = ""


class TuiDirectoryTrust(object):
    """管理启动阶段的目录信任确认界面。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_prompt: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
        get_max_height: typing.Callable[[], int]
    ) -> None:
        self.invalidate     = invalidate
        self.focus_prompt   = focus_prompt
        self.focus_input    = focus_input
        self.get_width      = get_width
        self.get_max_height = get_max_height

        self.state: DirectoryTrustState | None = None

        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回目录信任界面是否正在显示。"""
        return self.state is not None

    def begin(self, cwd: Path, trust_target: Path) -> None:
        """激活目录信任界面并准备接收选择。"""
        if self.state is not None:
            raise RuntimeError("directory trust prompt is already active")

        self.state = DirectoryTrustState(
            cwd=_safe_path(cwd),
            trust_target=_safe_path(trust_target),
            highlighted="trust",
            future=_new_future(),
        )
        self.focus_prompt()
        self.invalidate()

    async def wait(self) -> DirectoryTrustChoice:
        """等待当前目录信任选择。"""
        state = self.state
        if state is None:
            raise RuntimeError("directory trust prompt is not active")
        return await state.future

    def show_error(self, message: str) -> None:
        """显示保存失败信息并允许用户重新选择。"""
        state = self.state
        if state is None:
            return None

        state.error       = sanitize_terminal_text(message).strip()
        state.highlighted = "trust"
        state.future      = _new_future()

        self.invalidate()

    def close(self) -> None:
        """关闭目录信任界面并恢复主输入焦点。"""
        state = self.state
        if state is None:
            return None
        if not state.future.done():
            state.future.set_result("quit")
        self.state = None
        self.focus_input()
        self.invalidate()

    def fragments(self) -> StyleAndTextTuples:
        """生成与终端宽度匹配的目录信任界面片段。"""
        state = self.state
        if state is None:
            return []

        width = max(1, self.get_width())

        out: StyleAndTextTuples = [
            ("", "> "),
            ("class:directory-trust.title", "You are in "),
            ("", state.cwd),
            ("", "\n\n"),
        ]

        if state.cwd != state.trust_target:
            warning = (
                "Note: You're in a subdirectory of a Git project. Trusting "
                f"will apply to the repository root: {state.trust_target}"
            )
            out.extend(_paragraph_fragments(
                warning,
                width=width,
                style="class:directory-trust.warning",
            ))
            out.append(("", "\n\n"))

        body = (
            "Do you trust the contents of this directory? Working with "
            "untrusted contents comes with higher risk of prompt injection. "
            "Trusting the directory allows project-local config, hooks, and "
            "exec policies to load."
        )
        out.extend(_paragraph_fragments(
            body,
            width=width,
            style="class:directory-trust.body",
        ))
        out.append(("", "\n\n"))

        for index, (label, choice) in enumerate((
            ("Yes, continue", "trust"),
            ("No, quit", "quit"),
        ), start=1):
            selected = state.highlighted == choice
            style = (
                "class:directory-trust.option.selected"
                if selected
                else "class:directory-trust.option"
            )
            marker = "›" if selected else " "
            out.append((style, f"{marker} {index}. {label}"))
            out.append(("", "\n"))

        out.append(("", "\n"))

        if state.error:
            out.extend(_paragraph_fragments(
                state.error,
                width=width,
                style="class:directory-trust.error",
            ))
            out.append(("", "\n\n"))

        out.extend([
            ("class:directory-trust.hint", "  Press "),
            ("class:directory-trust.key", "enter"),
            ("class:directory-trust.hint", " to continue"),
        ])
        return out

    def height(self) -> int:
        """返回目录信任界面当前占用的显示行数。"""
        text = fragments_text(self.fragments())
        if not text:
            return 0
        return min(
            max(1, self.get_max_height()),
            max(1, text.count("\n") + 1),
        )

    def _move(self, choice: DirectoryTrustChoice) -> None:
        """更新高亮选择。"""
        state = self.state
        if state is None or state.future.done():
            return None
        state.highlighted = choice
        self.invalidate()

    def _choose(self, choice: DirectoryTrustChoice) -> None:
        """提交指定选择。"""
        state = self.state
        if state is None or state.future.done():
            return None
        state.highlighted = choice
        state.future.set_result(choice)

    def _choose_highlighted(self) -> None:
        """提交当前高亮选择。"""
        state = self.state
        if state is not None:
            self._choose(state.highlighted)

    def _build_key_bindings(self) -> KeyBindings:
        """创建目录信任界面的局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("up")
        @bindings.add("k")
        def _(event) -> None:
            _ = event
            self._move("trust")

        @bindings.add("down")
        @bindings.add("j")
        def _(event) -> None:
            _ = event
            self._move("quit")

        @bindings.add("1")
        @bindings.add("y")
        def _(event) -> None:
            _ = event
            self._choose("trust")

        @bindings.add("2")
        @bindings.add("n")
        @bindings.add("q")
        @bindings.add(Keys.Escape, eager=True)
        @bindings.add("c-c")
        @bindings.add("c-d")
        def _(event) -> None:
            _ = event
            self._choose("quit")

        @bindings.add("enter")
        def _(event) -> None:
            _ = event
            self._choose_highlighted()

        return bindings


def _new_future() -> asyncio.Future[DirectoryTrustChoice]:
    """创建绑定当前事件循环的选择结果。"""
    return asyncio.get_running_loop().create_future()


def _safe_path(path: Path) -> str:
    """把路径转换为单行终端安全文本。"""
    return " ".join(sanitize_terminal_text(str(path)).splitlines())


def _paragraph_fragments(
    text: str,
    *,
    width: int,
    style: str
) -> StyleAndTextTuples:
    """生成带两个字符左缩进的自动折行段落。"""
    content_width = max(1, width - 2)

    rows = _wrap_words(text, width=content_width)

    out: StyleAndTextTuples = []
    for index, row in enumerate(rows):
        if index:
            out.append(("", "\n"))
        out.append(("", "  "))
        out.append((style, row))

    return out


def _wrap_words(text: str, *, width: int) -> list[str]:
    """按终端显示宽度折行，并优先保留完整单词。"""
    limit = max(1, width)

    rows: list[str] = []

    current: str = ""

    for word in " ".join(text.split()).split(" "):
        candidate = f"{current} {word}" if current else word
        if get_cwidth(candidate) <= limit:
            current = candidate
            continue

        if current:
            rows.append(current)

        while get_cwidth(word) > limit:
            prefix = ""
            for unit in iter_text_units(word):
                if prefix and get_cwidth(prefix + unit) > limit:
                    break
                prefix += unit
            if not prefix:
                prefix = word[0]
            rows.append(prefix)
            word = word[len(prefix):]
        current = word

    if current or not rows:
        rows.append(current)
    return rows


if __name__ == '__main__':
    pass
