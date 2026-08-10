# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from dataclasses import dataclass
from prompt_toolkit.auto_suggest import (
    AutoSuggest,
    Suggestion
)
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import (
    CompleteEvent,
    Completion
)
from prompt_toolkit.document import Document
from prompt_toolkit.filters import (
    Condition,
    has_focus
)
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.named_commands import get_by_name
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from mind_core.skills import SkillSpec
from mind_app.presentation.terminal_text import sanitize_terminal_text
from ..prompting.commands import (
    SlashCommandCompleter,
    completion_changes_input,
    parameterized_command_texts
)
from ..prompting.ghost import (
    apply_ghost_prompt,
    iter_ghost_templates
)
from ..prompting.paste import (
    format_paste_placeholder,
    iter_paste_placeholders,
    parse_paste_placeholder,
    paste_line_count
)
from ..prompting.skills import SkillTokenLexer

INPUT_BUFFER_NAME = "prompt-input"


def _ignore_action() -> None:
    """忽略尚未绑定的输入动作。"""


def _ignore_buffer_action(_buffer: typing.Any) -> None:
    """忽略尚未绑定的输入区动作。"""


def _deny_action() -> bool:
    """拒绝尚未绑定的输入条件。"""
    return False


@dataclass(frozen=True, slots=True)
class TuiInputHistoryEntry(object):
    """保存一条可恢复编辑状态的输入历史。"""
    editable_text: str
    paste_items: tuple[tuple[str, str], ...] = ()
    shell_mode: bool = False

    @property
    def visible_text(self) -> str:
        """返回历史列表使用的可见文本。"""
        if not self.shell_mode:
            return self.editable_text
        command = self.editable_text.strip()
        return f"! {command}" if command else "!"

    @property
    def paste_store(self) -> dict[str, str]:
        """返回该历史项独立持有的粘贴映射。"""
        return dict(self.paste_items)


class TuiInputHistory(InMemoryHistory):
    """保存输入历史并允许撤销最近一次匹配的提交。"""

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[TuiInputHistoryEntry] = []

    @staticmethod
    def _plain_entry(text: str) -> TuiInputHistoryEntry:
        """把普通历史字符串转换为结构化输入状态。"""
        value = str(text or "")
        if value.startswith("!"):
            return TuiInputHistoryEntry(
                editable_text=value[1:].lstrip(" "),
                shell_mode=True,
            )
        return TuiInputHistoryEntry(editable_text=value)

    def append_string(self, string: str) -> None:
        """追加一条不带粘贴映射的普通历史。"""
        self._append_entry(self._plain_entry(string))

    def append_submission(
        self,
        editable_text: str,
        paste_store: dict[str, str],
        *,
        shell_mode: bool,
    ) -> bool:
        """追加一条提交历史并保留其独立编辑状态。"""
        paste_items = tuple(
            (placeholder, original)
            for placeholder, original in paste_store.items()
        )
        entry = TuiInputHistoryEntry(
            editable_text=str(editable_text or ""),
            paste_items=paste_items,
            shell_mode=bool(shell_mode),
        )
        if self._entries and self._entries[-1] == entry:
            return False
        self._append_entry(entry)
        return True

    def entries(self) -> tuple[TuiInputHistoryEntry, ...]:
        """返回按提交顺序排列的结构化历史。"""
        return tuple(self._entries)

    def _append_entry(self, entry: TuiInputHistoryEntry) -> None:
        """同步追加结构化状态和终端历史字符串。"""
        super().append_string(entry.visible_text)
        self._entries.append(entry)

    def rollback_latest(self, text: str) -> None:
        """仅在最后一项匹配时撤销对应历史记录。"""
        if self._storage and self._storage[-1] == text:
            self._storage.pop()
            if self._entries:
                self._entries.pop()
        if self._loaded_strings and self._loaded_strings[0] == text:
            self._loaded_strings.pop(0)


class TuiAutoSuggest(AutoSuggest):
    """生成 TUI 输入区的行内命令和模板建议。"""

    SLASH_HINTS: typing.Final[dict[str, str]] = {
        "/model"  : " <model-id>",
        "/model " : "<model-id>",
    }

    def __init__(self) -> None:
        self.shell_mode: bool = False
        self.ghost_templates  = iter_ghost_templates()

    def get_suggestion(self, buffer, document):
        """根据光标前文本返回一项行内建议。"""
        if self.shell_mode:
            return None
        if getattr(buffer, "complete_state", None) is not None:
            return None

        text         = document.text_before_cursor
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
        parameterized_command_texts()
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
    )

    PASTE_CHAR_THRESHOLD: typing.Final[int] = 1200
    PASTE_LINE_THRESHOLD: typing.Final[int] = 20

    def __init__(self) -> None:
        self.paste_store: dict[str, str]   = {}
        self.skills: tuple[SkillSpec, ...] = ()

        self.history   = TuiInputHistory()
        self.completer = SlashCommandCompleter(lambda: self.skills)

        self.auto_suggest = TuiAutoSuggest()

        self.lexer = SkillTokenLexer(
            self._active_paste_placeholders,
            lambda: self.skills,
        )

        self.interrupt_handler: typing.Callable[[], None]    = _ignore_action
        self.exit_handler: typing.Callable[[], None]         = _ignore_action
        self.input_resize_handler: typing.Callable[[], None] = _ignore_action

        self.can_exit: typing.Callable[[], bool]                     = _deny_action
        self.can_submit_queue: typing.Callable[[], bool]             = _deny_action
        self.can_rollback_queue: typing.Callable[[], bool]           = _deny_action
        self.can_backtrack_history: typing.Callable[[], bool]        = _deny_action
        self.can_report_missing_backtrack: typing.Callable[[], bool] = _deny_action

        self.rollback_queue_handler: typing.Callable[[], bool]    = _deny_action

        self.queue_submission_handler: typing.Callable[[typing.Any], None] = (
            _ignore_buffer_action
        )
        self.backtrack_history_handler: typing.Callable[[], None] = _ignore_action
        self.missing_backtrack_handler: typing.Callable[[], None] = _ignore_action

        self.shell_mode: bool = False

        self._shell_mode_undo_transition: tuple[str, str] | None = None

        self.history_backtrack_primed: bool = False

        self._history_entries: tuple[TuiInputHistoryEntry, ...] = ()
        self._history_index: int | None                         = None
        self._history_draft: Document | None                    = None
        self._history_draft_shell_mode: bool                    = False
        self._history_draft_paste_store: dict[str, str]         = {}

        self._dismissed_completion_query: tuple[str, int] | None = None

        self.key_bindings = self._build_key_bindings()

        self.style = Style.from_dict({
            "prompt": "#E2E5EA",
            "prompt.kicker": "bold #7B838E",
            "prompt.command.slash": "#C4A7E7",
            "prompt.model": "bold #F3F5F8",
            "prompt.muted": "bold #767D87",
            "prompt.access": "bold #8FC7EA",
            "prompt.access.full": "bold #D8B26E",
            "prompt.workspace": "bold #8A929C",
            "prompt.exec": "bold #8FC7EA",
            "prompt.exec.command": "dim #A8B1BB",
            "placeholder": "#727983",
            "auto-suggestion": "#5A616A",
            "skill-token": "bold #8FD7FF",
            "shell-escape": "bold #FF6B6B",
            "paste-placeholder": "bold #D3C27C",
            "completion-menu": "bg:default #B8C0C9",
            "completion-menu.completion": "bg:default bold #B8C0C9",
            "completion-menu.completion.current": "bg:default bold #F4F8FB",
            "completion-menu.meta.completion": "#707A84",
            "completion-menu.meta.completion.current": "#8FC7EA",
            "completion-menu.empty": "#59616A",
        })

    @staticmethod
    def theme() -> dict[str, str]:
        """返回 TUI 主题颜色。"""
        return {"brand": "#2DAA9E", "soft": "#1E7F78"}

    @staticmethod
    def apply_completion(buffer, completion: Completion) -> None:
        """应用补全，并让斜杠命令替换光标后的剩余输入。"""
        state    = buffer.complete_state
        document = state.original_document if state is not None else buffer.document
        cursor   = document.cursor_position
        start    = cursor + completion.start_position
        command  = document.text_before_cursor.lstrip().startswith("/")
        suffix   = "" if command else document.text_after_cursor
        text     = document.text[:start] + completion.text + suffix

        buffer.complete_state = None

        buffer.document = Document(
            text,
            cursor_position=start + len(completion.text),
        )

    @staticmethod
    def _history_entry_state(
        entry: TuiInputHistoryEntry
    ) -> tuple[str, bool]:
        """把历史条目转换为输入文本和 Shell 前缀状态。"""
        return entry.editable_text, entry.shell_mode

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

    def _selected_menu_completion(self, buffer) -> Completion | None:
        """返回当前补全菜单中准备确认的候选项。"""
        completions = self.completion_menu_completions(buffer.document)
        if not completions:
            return None

        state = buffer.complete_state
        if state is not None and state.current_completion is not None:
            return state.current_completion
        return completions[0]

    def _apply_menu_completion(self, buffer, completion: Completion) -> None:
        """应用菜单候选并处理后续补全状态。"""
        original = buffer.document
        self.apply_completion(buffer, completion)

        if buffer.document == original:
            self.dismiss_completion_menu(buffer)
        elif completion.text == "$":
            buffer.start_completion(
                select_first=False,
                complete_event=CompleteEvent(text_inserted=True),
            )
        elif completion.text in self.PARAMETERIZED_COMMANDS:
            buffer.suggestion = self.auto_suggest.get_suggestion(
                buffer,
                buffer.document,
            )
            buffer.on_suggestion_set.fire()

    def new_placeholder(self) -> str:
        """为新的输入轮次生成一次占位文案。"""
        prompt  = random.choice(self.PLACEHOLDER_PROMPTS)
        command = random.choice(self.PLACEHOLDER_COMMANDS)

        return f"{prompt}, {command}"

    def set_skills(self, skills: typing.Iterable[SkillSpec]) -> None:
        """更新输入补全和高亮使用的 skill 快照。"""
        self.skills = tuple(skills)

    def set_shell_mode(self, active: bool) -> None:
        """更新输入框的 Shell 前缀模式。"""
        self.shell_mode = bool(active)
        self.auto_suggest.shell_mode = self.shell_mode
        self._shell_mode_undo_transition = None

    def _promote_shell_prefix(
        self,
        buffer,
        *,
        previous_text: str,
    ) -> bool:
        """把编辑后暴露的 Shell 前缀提升为独立输入状态。"""
        document = buffer.document
        if self.shell_mode or not document.text.startswith("!"):
            return False

        remainder = document.text[1:]
        command   = remainder.lstrip(" ")
        removed   = 1 + len(remainder) - len(command)

        cursor = max(
            0,
            min(len(command), document.cursor_position - removed),
        )

        buffer.cancel_completion()
        self.set_shell_mode(True)
        buffer.document = Document(command, cursor_position=cursor)
        self._shell_mode_undo_transition = previous_text, command
        return True

    def _restore_shell_mode_after_undo(
        self,
        buffer,
        *,
        previous_text: str,
    ) -> bool:
        """在文本撤销跨过模式转换边界时恢复普通输入状态。"""
        transition = self._shell_mode_undo_transition
        if not self.shell_mode or transition is None:
            return False

        target, source = transition
        if previous_text != source or buffer.text != target:
            return False

        self.set_shell_mode(False)
        return True

    def _finish_destructive_edit(
        self,
        buffer,
        selected_text: str | None = None,
        *,
        previous_text: str,
    ) -> None:
        """收束删除后的输入模式、联想和补全状态。"""
        self._promote_shell_prefix(buffer, previous_text=previous_text)

        buffer.suggestion = self.auto_suggest.get_suggestion(
            buffer,
            buffer.document,
        )
        buffer.on_suggestion_set.fire()

        if buffer.completer and buffer.complete_while_typing():
            self.refresh_completion_menu(buffer, selected_text)

        self.input_resize_handler()

    def bind_interrupt(self, handler: typing.Callable[[], None]) -> None:
        """绑定主运行时提供的输入中断处理函数。"""
        self.interrupt_handler = handler

    def bind_input_resize(self, handler: typing.Callable[[], None]) -> None:
        """绑定编辑操作改变输入尺寸后执行的布局收束动作。"""
        self.input_resize_handler = handler

    def handle_interrupt(self, buffer) -> None:
        """优先关闭补全，再把取消操作交给主运行时。"""
        if (
            buffer.complete_state is not None
            or self.completion_menu_completions(buffer.document) is not None
        ):
            self.dismiss_completion_menu(buffer)
            return None

        self.interrupt_handler()
        self.input_resize_handler()

    def completion_menu_completions(
        self,
        document: Document
    ) -> tuple[Completion, ...] | None:
        """返回当前未被关闭的命令或 skill 菜单项。"""
        if self._completion_menu_dismissed(document):
            return None
        return self.completer.menu_completions(document)

    def _completion_menu_dismissed(self, document: Document) -> bool:
        """判断当前文本和光标位置是否已主动关闭补全。"""
        return (
            document.text,
            document.cursor_position,
        ) == self._dismissed_completion_query

    def reopen_completion_menu(self, buffer) -> None:
        """在输入内容变化后允许补全菜单重新显示。"""
        _ = buffer
        self._dismissed_completion_query = None

    def dismiss_completion_menu(self, buffer) -> None:
        """关闭当前补全菜单并保留输入内容。"""
        document = getattr(buffer, "document", None)
        if document is not None:
            self._dismissed_completion_query = (
                document.text,
                document.cursor_position,
            )
        buffer.cancel_completion()

    def select_default_completion(self, buffer) -> None:
        """让命令或 skill 菜单默认高亮第一个有效候选项。"""
        state = buffer.complete_state
        if (
            state is None
            or state.complete_index is not None
            or not state.completions
            or self.completion_menu_completions(state.original_document) is None
        ):
            return None

        if (
            len(state.completions) == 1
            and not completion_changes_input(
                state.original_document,
                state.completions[0],
            )
        ):
            return None

        state.go_to_index(0)

    def refresh_completion_menu(
        self,
        buffer,
        selected_text: str | None = None
    ) -> None:
        """同步刷新补全菜单并尽量保留当前候选项。"""
        if self.completion_menu_completions(buffer.document) is None:
            return None

        completions = self.completer.matching_completions(buffer.document)
        if not completions:
            return None

        index = next(
            (
                item_index
                for item_index, completion in enumerate(completions)
                if completion.text == selected_text
            ),
            0,
        )

        buffer.complete_state = CompletionState(
            original_document=buffer.document,
            completions=list(completions),
            complete_index=index,
        )
        buffer.on_completions_changed.fire()

    def refresh_inserted_completion_menu(self, buffer) -> None:
        """在字符插入后同步刷新可用的补全菜单。"""
        if buffer.completer and buffer.complete_while_typing():
            self.refresh_completion_menu(buffer)

    def bind_exit(
        self,
        can_exit: typing.Callable[[], bool],
        handler: typing.Callable[[], None]
    ) -> None:
        """绑定空输入状态下的直接退出判断和处理函数。"""
        self.can_exit = can_exit
        self.exit_handler = handler

    def bind_queue_rollback(
        self,
        can_rollback: typing.Callable[[], bool],
        handler: typing.Callable[[], bool]
    ) -> None:
        """绑定执行期待提交消息的可用状态和撤回处理。"""
        self.can_rollback_queue     = can_rollback
        self.rollback_queue_handler = handler

    def bind_queue_submission(
        self,
        can_submit: typing.Callable[[], bool],
        handler: typing.Callable[[typing.Any], None]
    ) -> None:
        """绑定执行期使用 Tab 提交待处理消息的可用状态。"""
        self.can_submit_queue         = can_submit
        self.queue_submission_handler = handler

    def bind_history_backtrack(
        self,
        can_backtrack: typing.Callable[[], bool],
        handler: typing.Callable[[], None],
        can_report_missing: typing.Callable[[], bool],
        missing_handler: typing.Callable[[], None]
    ) -> None:
        """绑定空输入状态下的历史编辑入口。"""
        self.can_backtrack_history        = can_backtrack
        self.backtrack_history_handler    = handler
        self.can_report_missing_backtrack = can_report_missing
        self.missing_backtrack_handler    = missing_handler

    def cancel_history_backtrack(self) -> None:
        """清除等待第二次 Esc 的历史编辑状态。"""
        self.history_backtrack_primed = False

    def submission_state(self, text: str | None = None) -> dict[str, str]:
        """返回当前提交文本关联的折叠粘贴状态。"""
        if text is not None:
            active = {
                placeholder
                for _start, _end, placeholder in iter_paste_placeholders(text)
            }
            return {
                placeholder: original
                for placeholder, original in self.paste_store.items()
                if placeholder in active
            }
        return dict(self.paste_store)

    def restore_submission_state(self, state: dict[str, str]) -> None:
        """恢复被撤回提交文本关联的折叠粘贴状态。"""
        self.paste_store = dict(state)

    def rollback_submission_history(self, text: str) -> None:
        """撤销最近一次匹配的输入历史提交。"""
        self.history.rollback_latest(text)

    def record_submission_history(
        self,
        editable_text: str,
        paste_store: dict[str, str],
        *,
        shell_mode: bool
    ) -> bool:
        """记录一次提交对应的完整可编辑历史。"""
        return self.history.append_submission(
            editable_text,
            paste_store,
            shell_mode=shell_mode,
        )

    def restore_submission(self, text: str) -> str:
        """还原折叠粘贴内容并清理提交文本。"""
        restored = text
        for placeholder, original in sorted(
            self.paste_store.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            restored = restored.replace(placeholder, original, 1)
        return restored.strip()

    def clear_submission_state(self) -> None:
        """清理一次提交关联的临时粘贴状态。"""
        self._clear_paste_state()
        self.set_shell_mode(False)
        self._reset_history_navigation()

    def _active_paste_placeholders(self) -> tuple[str, ...]:
        """返回输入框中仍然有效的折叠粘贴占位符。"""
        return tuple(self.paste_store)

    def _display_paste(self, text: str, current_text: str) -> str:
        """按体积决定直接展示或折叠粘贴内容。"""
        should_fold = (
            len(text) >= self.PASTE_CHAR_THRESHOLD
            or paste_line_count(text) >= self.PASTE_LINE_THRESHOLD
        )
        if not should_fold:
            return text

        active_placeholders = {
            placeholder
            for _start, _end, placeholder in iter_paste_placeholders(current_text)
        }

        self.paste_store = {
            placeholder: original
            for placeholder, original in self.paste_store.items()
            if placeholder in active_placeholders
        }

        occupied = {
            parsed.index
            for placeholder in self.paste_store
            if (parsed := parse_paste_placeholder(placeholder)) is not None
        }

        index: int = 1
        while True:
            while index in occupied:
                index += 1
            placeholder = format_paste_placeholder(text, index)
            if (
                placeholder not in self.paste_store
                and placeholder not in active_placeholders
            ):
                break
            occupied.add(index)

        self.paste_store[placeholder] = text
        return placeholder

    def _clear_paste_state(self) -> None:
        """清理粘贴内容映射。"""
        self.paste_store.clear()

    def _reset_history_navigation(self) -> None:
        """重置输入历史导航状态。"""
        self._history_entries           = ()
        self._history_index             = None
        self._history_draft             = None
        self._history_draft_shell_mode  = False
        self._history_draft_paste_store = {}

    def _start_history_navigation(self, buffer) -> None:
        """根据当前草稿创建输入历史导航快照。"""
        prefix = buffer.document.text_before_cursor
        if self.shell_mode:
            prefix = f"! {prefix}" if prefix else "!"

        self._history_draft             = buffer.document
        self._history_draft_shell_mode  = self.shell_mode
        self._history_draft_paste_store = dict(self.paste_store)

        self._history_entries = tuple(
            entry
            for entry in self.history.entries()
            if entry.visible_text.startswith(prefix)
        )

        self._history_index = len(self._history_entries)

    def _history_navigation_matches_buffer(self, buffer) -> bool:
        """判断输入框是否仍处于当前输入历史位置。"""
        index = self._history_index
        draft = self._history_draft

        if index is None or draft is None:
            return False

        if index == len(self._history_entries):
            return (
                buffer.text == draft.text
                and self.shell_mode == self._history_draft_shell_mode
                and self.paste_store == self._history_draft_paste_store
            )

        entry = self._history_entries[index]

        text, shell_mode = self._history_entry_state(entry)

        return bool(
            buffer.text == text
            and self.shell_mode == shell_mode
            and self.paste_store == entry.paste_store
        )

    def _navigate_history(self, buffer, *, step: int, count: int) -> None:
        """在真实输入历史和当前草稿之间导航。"""
        if not self._history_navigation_matches_buffer(buffer):
            self._start_history_navigation(buffer)

        index = self._history_index
        draft = self._history_draft

        if index is None or draft is None:
            return None

        target = min(
            len(self._history_entries),
            max(0, index + step * max(1, count)),
        )
        if target == index:
            return None

        self._history_index = target

        if target == len(self._history_entries):
            self.set_shell_mode(self._history_draft_shell_mode)
            self.restore_submission_state(self._history_draft_paste_store)
            buffer.document = draft
            self.dismiss_completion_menu(buffer)
            self.input_resize_handler()
            return None

        entry = self._history_entries[target]

        text, shell_mode = self._history_entry_state(entry)

        self.set_shell_mode(shell_mode)
        self.restore_submission_state(entry.paste_store)

        buffer.document = Document(text, cursor_position=len(text))
        self.dismiss_completion_menu(buffer)
        self.input_resize_handler()

    def _build_key_bindings(self) -> KeyBindings:
        """创建 TUI 输入区按键绑定。"""
        bindings = KeyBindings()

        @bindings.add(
            "c-c",
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            self.handle_interrupt(event.app.current_buffer)

        direct_exit = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(
                self.can_exit()
                and not get_app().current_buffer.text
                and get_app().current_buffer.complete_state is None
                and not self.shell_mode
            )
        )

        @bindings.add("c-d", eager=True, filter=direct_exit)
        def _(event) -> None:
            _ = event
            self.exit_handler()

        @bindings.add(
            "c-u",
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            buffer.cancel_completion()
            buffer.text = ""
            buffer.cursor_position = 0
            self._clear_paste_state()
            self.set_shell_mode(False)
            self.input_resize_handler()

        shell_mode_empty = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: self.shell_mode and not get_app().current_buffer.text
        )

        @bindings.add("backspace", eager=True, filter=shell_mode_empty)
        @bindings.add("escape", eager=True, filter=shell_mode_empty)
        def _(event) -> None:
            self.set_shell_mode(False)
            event.app.invalidate()

        completion_menu_open = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(
                self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is not None
            )
        )
        dismissed_completion_query = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: self._completion_menu_dismissed(
                get_app().current_buffer.document
            )
        )

        @bindings.add("escape", eager=True, filter=completion_menu_open)
        def _(event) -> None:
            self.dismiss_completion_menu(event.app.current_buffer)
            event.app.invalidate()

        edit_backspace = has_focus(INPUT_BUFFER_NAME) & ~shell_mode_empty

        @bindings.add("backspace", eager=True, filter=edit_backspace)
        def _(event) -> None:
            buffer = event.app.current_buffer
            state  = buffer.complete_state
            previous_text = buffer.text

            selected_text = (
                state.current_completion.text
                if state is not None and state.current_completion is not None
                else None
            )

            if event.arg < 0:
                deleted = buffer.delete(count=-event.arg)
            else:
                deleted = buffer.delete_before_cursor(count=event.arg)

            if not deleted:
                event.app.output.bell()
                return None

            self._finish_destructive_edit(
                buffer,
                selected_text,
                previous_text=previous_text,
            )

        @bindings.add(
            "delete",
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            deleted = buffer.delete(count=event.arg)

            if not deleted:
                event.app.output.bell()
                return None

            self._finish_destructive_edit(
                buffer,
                previous_text=previous_text,
            )

        @bindings.add(
            "c-w",
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text

            if buffer.selection_state:
                event.app.clipboard.set_data(buffer.cut_selection())
            else:
                get_by_name("unix-word-rubout").call(event)

            if buffer.text == previous_text:
                return None

            self._finish_destructive_edit(
                buffer,
                previous_text=previous_text,
            )

        @bindings.add("c-z", eager=True, save_before=lambda event: False)
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            buffer.undo()
            self._restore_shell_mode_after_undo(
                buffer,
                previous_text=previous_text,
            )
            self.input_resize_handler()

        @bindings.add(
            "left",
            eager=True,
            filter=dismissed_completion_query,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_position = buffer.cursor_position

            buffer.cursor_left(count=max(1, event.arg))

            if buffer.cursor_position != previous_position:
                self.reopen_completion_menu(buffer)
                self.refresh_completion_menu(buffer)

        @bindings.add("escape", "enter")
        @bindings.add("c-o")
        def _(event) -> None:
            event.app.current_buffer.insert_text("\n")

        queue_rollback = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(
                self.can_rollback_queue()
                and self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is None
            )
        )

        queue_rollback_reserved = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(
                self.can_submit_queue()
                and not self.can_rollback_queue()
                and self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is None
            )
        )

        history_backtrack = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(self.can_backtrack_history())
        )
        missing_backtrack = has_focus(INPUT_BUFFER_NAME) & Condition(
            lambda: bool(self.can_report_missing_backtrack())
        )

        @bindings.add(Keys.Escape, eager=True, filter=history_backtrack)
        def _(event) -> None:
            if self.history_backtrack_primed:
                self.history_backtrack_primed = False
                self.backtrack_history_handler()
            else:
                self.history_backtrack_primed = True
            event.app.invalidate()

        @bindings.add(Keys.Escape, eager=True, filter=missing_backtrack)
        def _(event) -> None:
            if self.history_backtrack_primed:
                self.history_backtrack_primed = False
                self.missing_backtrack_handler()
            else:
                self.history_backtrack_primed = True
            event.app.invalidate()

        @bindings.add("escape", "up", eager=True, filter=queue_rollback)
        @bindings.add(Keys.ShiftLeft, eager=True, filter=queue_rollback)
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()
            self.rollback_queue_handler()

        @bindings.add(
            "escape",
            "up",
            eager=True,
            filter=queue_rollback_reserved,
        )
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()

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

            menu_completion = self._selected_menu_completion(buffer)

            completion_menu_opened = (
                self.completion_menu_completions(buffer.document) is not None
            )

            if menu_completion is not None:
                self._apply_menu_completion(buffer, menu_completion)
            elif completion_menu_opened:
                return None
            elif (
                self.can_submit_queue()
                and (buffer.text.strip() or self.shell_mode)
            ):
                buffer.cancel_completion()
                self.queue_submission_handler(buffer)
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
            self._select_completion(
                event.app.current_buffer,
                -max(1, event.arg),
            )

        @bindings.add(Keys.BracketedPaste, eager=True)
        def _(event) -> None:
            buffer = event.app.current_buffer
            data = sanitize_terminal_text(event.data or "")
            buffer.cancel_completion()
            if not buffer.text and data.startswith("!"):
                self.set_shell_mode(True)
                data = data[1:].lstrip(" ")
            buffer.insert_text(self._display_paste(data, buffer.text))

        @bindings.add("enter")
        def _(event) -> None:
            buffer = event.app.current_buffer
            completion = self._selected_menu_completion(buffer)
            if completion is not None:
                self._apply_menu_completion(buffer, completion)
                if (
                    completion.text == "$"
                    or completion.text.startswith("$")
                    or completion.text in self.PARAMETERIZED_COMMANDS
                ):
                    return

            else:
                state = buffer.complete_state
                if state is not None and state.current_completion is not None:
                    completion = state.current_completion
                    self.apply_completion(buffer, completion)
                    if completion.text.startswith("$"):
                        return

            buffer.validate_and_handle()

        @bindings.add("up")
        def _(event) -> None:
            buffer = event.app.current_buffer

            if self.completion_menu_completions(buffer.document) is not None:
                self._select_completion(buffer, -max(1, event.arg))
            elif buffer.complete_state:
                self._select_completion(buffer, -max(1, event.arg))
            elif buffer.document.cursor_position_row > 0:
                buffer.cursor_up(count=max(1, event.arg))
            elif not buffer.selection_state:
                self._navigate_history(
                    buffer,
                    step=-1,
                    count=max(1, event.arg),
                )

        @bindings.add("down")
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self.completion_menu_completions(buffer.document) is not None:
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.complete_state:
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.document.cursor_position_row < buffer.document.line_count - 1:
                buffer.cursor_down(count=max(1, event.arg))
            elif not buffer.selection_state:
                self._navigate_history(
                    buffer,
                    step=1,
                    count=max(1, event.arg),
                )

        @bindings.add(
            Keys.ControlUp,
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            self._navigate_history(
                event.app.current_buffer,
                step=-1,
                count=max(1, event.arg),
            )

        @bindings.add(
            Keys.ControlDown,
            eager=True,
            filter=has_focus(INPUT_BUFFER_NAME),
        )
        def _(event) -> None:
            self._navigate_history(
                event.app.current_buffer,
                step=1,
                count=max(1, event.arg),
            )

        return bindings


if __name__ == '__main__':
    pass
