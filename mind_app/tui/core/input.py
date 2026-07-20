# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from prompt_toolkit.auto_suggest import (
    AutoSuggest,
    Suggestion
)
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from ..prompting.commands import SlashCommandCompleter
from ..prompting.ghost import (
    apply_ghost_prompt,
    iter_ghost_templates
)
from ..prompting.skills import SkillTokenLexer

INPUT_BUFFER_NAME = "mind-input"


class TuiInputHistory(InMemoryHistory):
    """保存输入历史并允许撤销最近一次匹配的提交。"""

    def rollback_latest(self, text: str) -> None:
        """仅在最后一项匹配时撤销对应历史记录。"""
        if self._storage and self._storage[-1] == text:
            self._storage.pop()


class TuiAutoSuggest(AutoSuggest):
    """生成 TUI 输入区的行内命令和模板建议。"""

    SLASH_HINTS: typing.Final[dict[str, str]] = {
        "/attach": " <path>",
        "/attach ": "<path>",
        "/detach": " <index-or-path>",
        "/detach ": "<index-or-path>",
        "/model": " <model-id>",
        "/model ": "<model-id>",
    }

    def __init__(self) -> None:
        self.mode = "chat"
        self.shell_mode = False
        self.ghost_templates = iter_ghost_templates()

    def set_mode(self, mode: str) -> None:
        """更新自动建议使用的运行模式。"""
        self.mode = mode

    def get_suggestion(self, buffer, document):
        """根据光标前文本返回一项行内建议。"""
        if self.shell_mode:
            return None
        if getattr(buffer, "complete_state", None) is not None:
            return None
        text = document.text_before_cursor
        current_line = text.splitlines()[-1] if text.splitlines() else text
        if text.endswith("\n"):
            current_line = ""
        if not current_line:
            return None
        if current_line.startswith("/"):
            hint = self.SLASH_HINTS.get(current_line)
            return Suggestion(hint) if hint else None

        candidate = apply_ghost_prompt(current_line, self.ghost_templates)
        if candidate != current_line:
            return Suggestion(candidate[len(current_line):])
        return None


class TuiInputModel(object):
    """提供 TUI 独立的输入编辑、补全、历史和主题状态。"""

    PARAMETERIZED_COMMANDS: typing.Final[tuple[str, ...]] = (
        "/attach ",
        "/detach ",
        "/model ",
    )
    PLACEHOLDER_PROMPTS: typing.Final[tuple[str, ...]] = (
        "Ask anything",
        "Describe a goal",
        "Paste context",
        "Request a change",
        "Inspect something",
    )
    PLACEHOLDER_COMMANDS: typing.Final[tuple[str, ...]] = (
        "/ opens commands",
        "$ opens skills",
        "/permissions changes access",
        "/resume restores a session",
        "/new starts fresh",
        "/attach adds files",
        "! opens local shell",
        "! <cmd> runs shell once",
    )
    PASTE_CHAR_THRESHOLD: typing.Final[int] = 1200
    PASTE_LINE_THRESHOLD: typing.Final[int] = 20

    def __init__(self) -> None:
        self.history = TuiInputHistory()
        self.completer = SlashCommandCompleter()
        self.auto_suggest = TuiAutoSuggest()
        self.lexer = SkillTokenLexer()
        self.interrupt_handler: typing.Callable[[], None] | None = None
        self.can_submit_queue: typing.Callable[[], bool] | None = None
        self.can_rollback_queue: typing.Callable[[], bool] | None = None
        self.rollback_queue_handler: typing.Callable[[], bool] | None = None
        self.paste_store: dict[str, str] = {}
        self.shell_mode = False
        self.key_bindings = self._build_key_bindings()
        self.style = Style.from_dict({
            "prompt": "bold #E2E5EA",
            "prompt.kicker": "bold #7B838E",
            "prompt.model": "bold #F3F5F8",
            "prompt.muted": "bold #767D87",
            "prompt.access": "bold #8FC7EA",
            "prompt.access.full": "bold #D8B26E",
            "prompt.workspace": "bold #8A929C",
            "prompt.exec": "bold #8FC7EA",
            "prompt.exec.command": "dim #A8B1BB",
            "placeholder": "bold #727983",
            "auto-suggestion": "#5A616A",
            "skill-token": "bold #8FD7FF",
            "shell-escape": "bold #FF6B6B",
            "paste-placeholder": "bold #D3C27C",
            "completion-menu": "bg:default #D8DCE2",
            "completion-menu.completion": "bg:default bold #D6DBE2",
            "completion-menu.completion.current": "bg:default bold #F4F7FA",
            "completion-menu.meta.completion": "#7D858F",
            "completion-menu.meta.completion.current": "#AFC7D8",
        })

    def theme(self, mode: str) -> dict[str, str]:
        """返回运行模式对应的 TUI 颜色和标签。"""
        themes = {
            "chat": {"brand": "#4F8FC8", "soft": "#2F6FAD", "label": "Chat"},
            "fast": {"brand": "#4FA37D", "soft": "#2E7D5B", "label": "Fast"},
            "xtra": {"brand": "#2DAA9E", "soft": "#1E7F78", "label": "Xtra"},
        }
        return dict(themes[mode])

    def new_placeholder(self, mode: str) -> str:
        """为新的输入轮次生成一次占位文案。"""
        theme = self.theme(mode)
        prompt = random.choice(self.PLACEHOLDER_PROMPTS)
        command = random.choice(self.PLACEHOLDER_COMMANDS)
        return f"{theme['label']}, {prompt}, {command}"

    def set_mode(self, mode: str) -> None:
        """更新输入建议使用的运行模式。"""
        self.auto_suggest.set_mode(mode)

    def set_shell_mode(self, active: bool) -> None:
        """更新输入框的 Shell 前缀模式。"""
        self.shell_mode = bool(active)
        self.auto_suggest.shell_mode = self.shell_mode

    def bind_interrupt(self, handler: typing.Callable[[], None]) -> None:
        """绑定主运行时提供的输入中断处理函数。"""
        self.interrupt_handler = handler

    def bind_queue_rollback(
        self,
        can_rollback: typing.Callable[[], bool],
        handler: typing.Callable[[], bool],
    ) -> None:
        """绑定执行期待提交消息的可用状态和撤回处理。"""
        self.can_rollback_queue = can_rollback
        self.rollback_queue_handler = handler

    def bind_queue_submission(self, can_submit: typing.Callable[[], bool]) -> None:
        """绑定执行期使用 Tab 提交待处理消息的可用状态。"""
        self.can_submit_queue = can_submit

    def submission_state(self) -> dict[str, str]:
        """返回当前提交文本关联的折叠粘贴状态。"""
        return dict(self.paste_store)

    def restore_submission_state(self, state: dict[str, str]) -> None:
        """恢复被撤回提交文本关联的折叠粘贴状态。"""
        self.paste_store = dict(state)

    def rollback_submission_history(self, text: str) -> None:
        """撤销最近一次匹配的输入历史提交。"""
        self.history.rollback_latest(text)

    def restore_submission(self, text: str) -> str:
        """还原折叠粘贴内容并清理提交文本。"""
        restored = text
        for placeholder, original in sorted(
            self.paste_store.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            restored = restored.replace(placeholder, original)
        return restored.strip()

    def clear_submission_state(self) -> None:
        """清理一次提交关联的临时粘贴状态。"""
        self.paste_store.clear()
        self.set_shell_mode(False)

    def _display_paste(self, text: str, current_text: str) -> str:
        """按体积决定直接展示或折叠粘贴内容。"""
        should_fold = (
            len(text) >= self.PASTE_CHAR_THRESHOLD
            or len(text.splitlines()) >= self.PASTE_LINE_THRESHOLD
        )
        if not should_fold:
            return text
        self.paste_store = {
            placeholder: original
            for placeholder, original in self.paste_store.items()
            if placeholder in current_text
        }
        index = len(self.paste_store) + 1
        suffix = "" if index == 1 else f" #{index}"
        placeholder = f"[Pasted Content {len(text)} chars]{suffix}"
        self.paste_store[placeholder] = text
        return placeholder

    @staticmethod
    def _select_completion(buffer, step: int) -> bool:
        """只移动补全高亮，不提前改写输入内容。"""
        state = getattr(buffer, "complete_state", None)
        if state is None or not state.completions:
            return False
        current = state.complete_index
        if current is None:
            index = 0 if step > 0 else len(state.completions) - 1
        else:
            index = (current + step) % len(state.completions)
        state.go_to_index(index)
        buffer.on_completions_changed.fire()
        if buffer.suggestion is not None:
            buffer.suggestion = None
            buffer.on_suggestion_set.fire()
        return True

    def _build_key_bindings(self) -> KeyBindings:
        """创建 TUI 输入区按键绑定。"""
        bindings = KeyBindings()

        @bindings.add(
            "c-c",
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()
            if self.interrupt_handler is not None:
                self.interrupt_handler()

        @bindings.add("c-u", eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            buffer.cancel_completion()
            buffer.text = ""
            buffer.cursor_position = 0
            self.paste_store.clear()
            self.set_shell_mode(False)

        shell_mode_empty = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: self.shell_mode and not get_app().current_buffer.text
        )

        @bindings.add("backspace", eager=True, filter=shell_mode_empty)
        @bindings.add("escape", eager=True, filter=shell_mode_empty)
        def _(event) -> None:
            self.set_shell_mode(False)
            event.app.invalidate()

        @bindings.add("c-z", eager=True, save_before=lambda event: False)
        def _(event) -> None:
            event.app.current_buffer.undo()

        @bindings.add("escape", "enter")
        @bindings.add("c-o")
        def _(event) -> None:
            event.app.current_buffer.insert_text("\n")

        queue_rollback = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(
                self.can_rollback_queue is not None
                and self.can_rollback_queue()
            )
        )

        @bindings.add(Keys.Escape, eager=True, filter=queue_rollback)
        @bindings.add(Keys.ControlLeft, eager=True, filter=queue_rollback)
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()
            if self.rollback_queue_handler is not None:
                self.rollback_queue_handler()

        @bindings.add("/", eager=True)
        @bindings.add("$", eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            buffer.insert_text(event.data)
            if self.shell_mode:
                return None
            buffer.start_completion(
                select_first=False,
                complete_event=CompleteEvent(text_inserted=True),
            )

        @bindings.add("!", eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            if not self.shell_mode and not buffer.text:
                buffer.cancel_completion()
                self.set_shell_mode(True)
                event.app.invalidate()
                return None
            buffer.insert_text("!")

        @bindings.add("tab")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if (
                self.can_submit_queue is not None
                and self.can_submit_queue()
                and (buffer.text.strip() or self.shell_mode)
            ):
                buffer.cancel_completion()
                buffer.validate_and_handle()
            elif self.shell_mode:
                buffer.insert_text("    ")
            elif buffer.suggestion and buffer.suggestion.text:
                buffer.insert_text(buffer.suggestion.text)
            elif buffer.complete_state:
                self._select_completion(buffer, max(1, event.arg))
            else:
                buffer.start_completion(
                    select_first=True,
                    complete_event=CompleteEvent(completion_requested=True),
                )

        @bindings.add("s-tab")
        def _(event) -> None:
            self._select_completion(event.app.current_buffer, -max(1, event.arg))

        @bindings.add(Keys.BracketedPaste, eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            data = (event.data or "").replace("\r\n", "\n").replace("\r", "\n")
            buffer.cancel_completion()
            if not buffer.text and data.startswith("!"):
                self.set_shell_mode(True)
                data = data[1:].lstrip(" ")
            buffer.insert_text(self._display_paste(data, buffer.text))

        @bindings.add("enter")
        def _(event) -> None:
            buffer = event.app.current_buffer
            state = buffer.complete_state
            if state is not None and state.current_completion is not None:
                completion = state.current_completion
                buffer.apply_completion(completion)
                if (
                    completion.text == "$"
                    or completion.text.startswith("$")
                    or completion.text in self.PARAMETERIZED_COMMANDS
                ):
                    if completion.text == "$":
                        buffer.start_completion(
                            select_first=False,
                            complete_event=CompleteEvent(text_inserted=True),
                        )
                    return
            buffer.validate_and_handle()

        @bindings.add("up")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                self._select_completion(buffer, -max(1, event.arg))
            elif buffer.document.cursor_position_row > 0:
                buffer.cursor_up(count=max(1, event.arg))
            elif not buffer.selection_state:
                buffer.history_backward(count=max(1, event.arg))
                self._restore_shell_history(buffer)

        @bindings.add("down")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.document.cursor_position_row < buffer.document.line_count - 1:
                buffer.cursor_down(count=max(1, event.arg))
            elif not buffer.selection_state:
                buffer.history_forward(count=max(1, event.arg))
                self._restore_shell_history(buffer)

        return bindings

    def _restore_shell_history(self, buffer) -> None:
        """把历史中的 Shell 标记恢复为输入框前缀状态。"""
        text = buffer.text
        if text.startswith("!"):
            self.set_shell_mode(True)
            buffer.text = text[1:].lstrip(" ")
            buffer.cursor_position = len(buffer.text)
        else:
            self.set_shell_mode(False)


if __name__ == '__main__':
    pass
