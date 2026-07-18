# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import StyleAndTextTuples

if typing.TYPE_CHECKING:
    from ..state import TuiState

STATUS_FRAMES = ("•", "◦")


class BottomPaneRenderer:
    """生成输入区、队列、状态和页脚的展示内容。"""

    def __init__(
        self,
        *,
        state: "TuiState",
        input_buffer: Buffer,
        model_label: str,
        workspace_label: str
    ) -> None:
        """初始化底部区域的状态和固定标签。"""
        self.state           = state
        self.input_buffer    = input_buffer
        self.model_label     = model_label
        self.workspace_label = workspace_label

    def prompt_symbol(self) -> str:
        """返回当前输入模式的提示符。"""
        return "!" if self.state.composer.shell_mode else "›"

    def prompt_fragments(self) -> StyleAndTextTuples:
        """生成输入框提示符。"""
        style = (
            "class:input.prompt.shell"
            if self.state.composer.shell_mode
            else "class:input.prompt"
        )
        return [(style, self.prompt_symbol()), ("", " ")]

    def queue_fragments(self) -> StyleAndTextTuples:
        """生成等待提交队列面板。"""
        items = self.state.turn.queued_messages
        if not items:
            return []

        fragments: StyleAndTextTuples = [
            (
                "class:queue.title",
                "• Messages to be submitted after next tool call "
                "(press esc to interrupt and send immediately)"
            )
        ]
        for item in items[:5]:
            text = " ".join(item.text.split())
            fragments.extend([
                ("", "\n"),
                ("class:queue.arrow", "  ↳ "),
                ("class:queue.text", text)
            ])
        if len(items) > 5:
            fragments.extend([
                ("", "\n"),
                ("class:queue.more", f"    … {len(items) - 5} more")
            ])
        return fragments

    def composer_hint_fragments(self) -> StyleAndTextTuples:
        """生成输入区的排队提示。"""
        if not self.composer_hint_visible():
            return []
        return [("class:composer.hint", "  tab to queue message")]

    def composer_hint_visible(self) -> bool:
        """判断输入区是否需要显示排队提示。"""
        if (
            not self.state.bottom_pane.composer_visible
            or not self.state.turn.busy
            or not self.input_buffer.text.strip()
        ):
            return False
        completion_state = self.input_buffer.complete_state
        return not (
            completion_state is not None
            and completion_state.completions
        )

    def status_visible(self) -> bool:
        """判断固定状态区是否需要显示。"""
        return (
            bool(self.state.status.text)
            and not self.state.turn.queued_messages
            and self.state.bottom_pane.composer_visible
        )

    def status_fragments(self) -> StyleAndTextTuples:
        """生成固定但默认隐藏的动画状态行。"""
        if not self.status_visible():
            return []

        frame = STATUS_FRAMES[self.state.status.phase % len(STATUS_FRAMES)]

        return [
            ("class:status.glyph", f"{frame} "),
            ("class:status.text", self.state.status.text)
        ]

    def footer_fragments(self) -> StyleAndTextTuples:
        """生成固定页脚信息。"""
        return [
            ("class:footer.model", self.model_label),
            ("class:footer.separator", " · "),
            ("class:footer.workspace", self.workspace_label)
        ]


if __name__ == '__main__':
    pass
