# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from prompt_toolkit.auto_suggest import (
    AutoSuggest,
    Suggestion
)
from prompt_toolkit.completion import CompleteEvent
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
        self.ghost_templates = iter_ghost_templates()

    def set_mode(self, mode: str) -> None:
        """更新自动建议使用的运行模式。"""
        self.mode = mode

    def get_suggestion(self, buffer, document):
        """根据光标前文本返回一项行内建议。"""
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
        self.history = InMemoryHistory()
        self.completer = SlashCommandCompleter()
        self.auto_suggest = TuiAutoSuggest()
        self.lexer = SkillTokenLexer()
        self.paste_store: dict[str, str] = {}
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
            "completion-menu": "#D8DCE2",
            "completion-menu.completion": "bold #D6DBE2",
            "completion-menu.completion.current": "bold underline #F4F7FA",
            "completion-menu.meta.completion": "#7D858F",
            "completion-menu.meta.completion.current": "underline #D9E0E7",
            "scrollbar.background": "",
            "scrollbar.button": "#666D76",
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

        @bindings.add("c-u", eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            buffer.cancel_completion()
            buffer.text = ""
            buffer.cursor_position = 0
            self.paste_store.clear()

        @bindings.add("c-z", eager=True, save_before=lambda event: False)
        def _(event) -> None:
            event.app.current_buffer.undo()

        @bindings.add("escape", "enter")
        @bindings.add("c-o")
        def _(event) -> None:
            event.app.current_buffer.insert_text("\n")

        @bindings.add("/", eager=True)
        @bindings.add("$", eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            buffer.insert_text(event.data)
            buffer.start_completion(
                select_first=False,
                complete_event=CompleteEvent(text_inserted=True),
            )

        @bindings.add("tab")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if buffer.suggestion and buffer.suggestion.text:
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

        @bindings.add("down")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.document.cursor_position_row < buffer.document.line_count - 1:
                buffer.cursor_down(count=max(1, event.arg))
            elif not buffer.selection_state:
                buffer.history_forward(count=max(1, event.arg))

        return bindings


if __name__ == '__main__':
    pass
