# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.application.current import (
    get_app,
    get_app_or_none,
)
from prompt_toolkit.auto_suggest import AutoSuggest
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import (
    CompleteEvent,
    Completion,
)
from prompt_toolkit.document import Document
from prompt_toolkit.filters import (
    Condition,
    has_focus,
)
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.named_commands import get_by_name
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from frontends.terminal.text import sanitize_terminal_text
from infrastructure.skills import SkillSpec
from .interrupt import InterruptDisposition
from .keymap import (
    TuiRuntimeKeymap,
    bind_key_action,
)
from .token_menu import (
    CommittedTokenQuery,
    DismissedToken,
    TokenMenuItem,
    TokenMenuKind,
    TokenMenuSnapshot,
    TokenMenuState,
    prefix_match_indices,
    subsequence_match_indices,
)
from ..prompting.commands import (
    SlashCommandCompleter,
    completion_changes_input,
    is_first_input_line,
    parameterized_command_texts,
    slash_command_dismissal_token,
    slash_command_query,
)
from ..prompting.files import (
    FileSearchManager,
    file_category,
)
from ..prompting.paste import (
    format_paste_placeholder,
    iter_paste_placeholders,
    parse_paste_placeholder,
    paste_line_count,
)
from ..prompting.skills import (
    SKILL_SIGILS,
    SkillTokenLexer,
    iter_known_skill_tokens,
    skill_query_token,
)

INPUT_BUFFER_NAME = "prompt-input"

SKILL_SEARCH_MODES: typing.Final[tuple[str, ...]] = (
    "All Results",
    "Filesystem Only",
    "Plugins",
)


def _ignore_action() -> None:
    """忽略尚未绑定的输入动作。"""


def _ignore_interrupt() -> InterruptDisposition:
    """返回尚未绑定的输入中断结果。"""
    return InterruptDisposition.IGNORED


def _ignore_input_layout() -> None:
    """忽略尚未绑定的输入布局通知。"""


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


@dataclass(frozen=True, slots=True)
class TuiHistorySearchSnapshot(object):
    """描述 footer 当前显示的历史搜索状态。"""
    query: str
    status: typing.Literal["idle", "match", "no_match"]


@dataclass(slots=True)
class _TuiHistorySearchSession(object):
    """保存一次历史搜索独占的草稿与遍历位置。"""
    original_document: Document
    original_paste_items: tuple[tuple[str, str], ...]
    original_shell_mode: bool
    query: str = ""
    matches: tuple[TuiInputHistoryEntry, ...] = ()
    match_index: int | None = None


@dataclass(frozen=True, slots=True)
class _TuiKillBuffer(object):
    """保存可粘回文本及其折叠粘贴映射。"""
    text: str = ""
    paste_items: tuple[tuple[str, str], ...] = ()


class TuiInputHistory(InMemoryHistory):
    """保存输入历史并允许撤销最近一次匹配的提交。"""

    def __init__(self) -> None:
        super().__init__()

        self._entries: list[TuiInputHistoryEntry] = []
        self._pending_auto_text: str | None = None

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

    def _suppress_next_automatic_append(self, text: str) -> None:
        """抑制 prompt-toolkit 随提交自动重复写入的编辑文本。"""
        self._pending_auto_text = str(text or "")

    def _append_entry(self, entry: TuiInputHistoryEntry) -> None:
        """同步追加结构化状态和终端历史字符串。"""
        super().append_string(entry.visible_text)
        self._entries.append(entry)

    def append_string(self, string: str) -> None:
        """追加一条不带粘贴映射的普通历史。"""
        value = str(string or "")
        if self._pending_auto_text is not None:
            expected = self._pending_auto_text
            self._pending_auto_text = None
            if value == expected:
                return None
        self._append_entry(self._plain_entry(value))

    def append_submission(
        self,
        editable_text: str,
        paste_store: dict[str, str],
        *,
        shell_mode: bool
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

    def rollback_latest(
        self,
        text: str,
        *,
        alternate_text: str | None = None,
    ) -> None:
        """仅在最后一项匹配时撤销对应历史记录。"""
        self._pending_auto_text = None
        candidates = {str(text)}
        if alternate_text is not None:
            candidates.add(str(alternate_text))

        if self._storage and self._storage[-1] in candidates:
            self._storage.pop()
            if self._entries:
                self._entries.pop()
        if self._loaded_strings and self._loaded_strings[0] in candidates:
            self._loaded_strings.pop(0)


class TuiAutoSuggest(AutoSuggest):
    """生成 TUI 输入区的行内命令建议。"""

    def __init__(self) -> None:
        self.shell_mode: bool = False

    def get_suggestion(self, buffer, document):
        """根据光标前文本返回一项行内建议。"""
        if self.shell_mode:
            return None
        if not is_first_input_line(document):
            return None
        if getattr(buffer, "complete_state", None) is not None:
            return None

        text = document.text_before_cursor
        current_line = text.splitlines()[-1] if text.splitlines() else text

        if text.endswith("\n"):
            current_line = ""
        if not current_line:
            return None
        return None


class TuiInputModel(object):
    """提供 TUI 独立的输入编辑、补全、历史和主题状态。"""

    PARAMETERIZED_COMMANDS: typing.Final[tuple[str, ...]] = (
        parameterized_command_texts()
    )

    TAB_DISPATCH_COMMANDS: typing.Final[tuple[str, ...]] = ("/skills",)

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

    def __init__(
        self,
        *,
        workspace_root: Path | str | None = None,
        keymap: TuiRuntimeKeymap | None = None,
    ) -> None:
        self.paste_store: dict[str, str] = {}
        self.skills: tuple[SkillSpec, ...] = ()
        self.workspace_root: Path | None = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        self.file_search = FileSearchManager()
        self.history = TuiInputHistory()
        self.completer = SlashCommandCompleter(
            lambda: self.skills,
            lambda: self.workspace_root,
            self.file_search,
        )
        self.auto_suggest = TuiAutoSuggest()
        self.lexer = SkillTokenLexer(
            self._active_paste_placeholders,
            lambda: self.skills,
        )
        self.interrupt_handler: typing.Callable[[], InterruptDisposition] = (
            _ignore_interrupt
        )
        self.can_interrupt_turn: typing.Callable[[], bool] = (
            _deny_action
        )
        self.turn_interrupt_handler: typing.Callable[
            [], InterruptDisposition
        ] = _ignore_interrupt
        self.copy_last_response_handler: typing.Callable[[], None] = (
            _ignore_action
        )
        self.shortcut_help_handler: typing.Callable[[], None] = _ignore_action
        self.exit_handler: typing.Callable[[], None] = _ignore_action
        self._input_activity_handler: typing.Callable[[], None] = (
            _ignore_action
        )
        self._interrupt_dispatched: bool = False
        self._input_layout_handler: typing.Callable[[], None] = (
            _ignore_input_layout
        )
        self.can_exit: typing.Callable[[], bool] = _deny_action
        self.can_submit_queue: typing.Callable[[], bool] = _deny_action
        self.can_rollback_queue: typing.Callable[[], bool] = _deny_action
        self.can_backtrack_history: typing.Callable[[], bool] = _deny_action
        self.can_report_missing_backtrack: typing.Callable[[], bool] = _deny_action
        self.rollback_queue_handler: typing.Callable[[], bool] = _deny_action
        self.queue_submission_handler: typing.Callable[[typing.Any], None] = (
            _ignore_buffer_action
        )
        self.backtrack_history_handler: typing.Callable[[], None] = _ignore_action
        self.missing_backtrack_handler: typing.Callable[[], None] = _ignore_action
        self.shell_mode: bool = False
        self._shell_mode_undo_transition: tuple[str, str] | None = None
        self.history_backtrack_primed: bool = False
        self._history_entries: tuple[TuiInputHistoryEntry, ...] = ()
        self._history_index: int | None = None
        self._history_completion_dismissed: bool = False
        self._token_menu_state: TokenMenuState = TokenMenuState()
        self._skill_search_mode_index: int = 0
        self._mention_popup_active: bool = False
        self._history_search: _TuiHistorySearchSession | None = None
        self._kill_buffer = _TuiKillBuffer()
        self.keymap = keymap or TuiRuntimeKeymap.defaults()

        self.key_bindings = self._build_key_bindings()

        self.style = Style.from_dict({
            "prompt": "",
            "prompt.kicker": "bold nodim",
            "prompt.command.slash": "",
            "prompt.model": "bold",
            "prompt.muted": "bold",
            "prompt.access": "bold",
            "prompt.access.full": "bold",
            "prompt.workspace": "bold",
            "prompt.exec": "bold",
            "prompt.exec.command": "dim",
            "placeholder": "",
            "auto-suggestion": "",
            "skill-token": "nodim",
            "shell-escape": "bold",
            "paste-placeholder": "bold",
            "completion-menu": "bg:default",
            "completion-menu.completion": "bg:default bold",
            "completion-menu.completion.current": "bg:default bold",
            "completion-menu.meta.completion": "",
            "completion-menu.meta.completion.current": "bg:default bold",
            "completion-menu.empty": "dim italic",
            "completion-menu.empty.mention": "italic nodim",
            "token-menu": "",
            "token-menu.command": "",
            "token-menu.command.current": "bold nodim",
            "token-menu.skill": "dim",
            "token-menu.skill.current": "bold nodim",
            "token-menu.skill-mention": "dim",
            "token-menu.skill-mention.current": "bold nodim",
            "token-menu.plugin-mention": "",
            "token-menu.plugin-mention.current": "bold nodim",
            "token-menu.file-mention": "",
            "token-menu.file-mention.current": "bold nodim",
            "token-menu.directory-mention": "",
            "token-menu.directory-mention.current": "bold nodim",
            "token-menu.completion": "bold",
            "token-menu.completion.current": "bold nodim",
            "token-menu.meta.command": "dim",
            "token-menu.meta.command.current": "bold nodim",
            "token-menu.meta.skill": "dim",
            "token-menu.meta.skill.current": "bold nodim",
            "token-menu.meta.skill-mention": "dim",
            "token-menu.meta.skill-mention.current": "bold nodim",
            "token-menu.meta.plugin-mention": "dim",
            "token-menu.meta.plugin-mention.current": "bold nodim",
            "token-menu.meta.file-mention": "dim",
            "token-menu.meta.file-mention.current": "bold nodim",
            "token-menu.meta.directory-mention": "dim",
            "token-menu.meta.directory-mention.current": "bold nodim",
            "token-menu.meta.completion": "dim",
            "token-menu.meta.completion.current": "bold nodim",
            "tui-menu.footer.right.plugins.current": "bold nodim",
            "footer.search-label": "dim",
            "footer.search-query": "bold",
            "footer.search-status": "dim",
            "footer.search-error": "bold",
        })

    def set_keymap(self, keymap: TuiRuntimeKeymap) -> None:
        """替换输入区使用的不可变运行时按键快照。"""
        self.keymap = keymap
        self.key_bindings = self._build_key_bindings()

    @property
    def skill_search_mode(self) -> str:
        """返回 `@` popup 当前搜索模式名称。"""
        return SKILL_SEARCH_MODES[self._skill_search_mode_index]

    @staticmethod
    def _history_entry_state(entry: TuiInputHistoryEntry) -> tuple[str, bool]:
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

    @staticmethod
    def _token_menu_kind(
        document: Document,
        completion: Completion,
    ) -> TokenMenuKind:
        """返回补全候选在 token 菜单中的类型。"""
        query = skill_query_token(document.text_before_cursor)
        category = file_category(completion.display_meta_text)
        tool_category = completion.display_meta_text.split(None, 1)[0]
        if query and query.startswith("@") and category == "File":
            return "file-mention"
        if query and query.startswith("@") and category == "Dir":
            return "directory-mention"

        if completion.text[:1] in SKILL_SIGILS:
            if query and query.startswith("@"):
                if tool_category == "Plugin":
                    return "plugin-mention"
                return "skill-mention"
            return "skill"
        if completion.text.startswith("/"):
            return "command"
        return "completion"

    @staticmethod
    def _skill_completion_token(document: Document) -> tuple[int, str] | None:
        """返回当前 skill 补全 token 的起点和完整文本。"""
        prefix = skill_query_token(document.text_before_cursor)
        if prefix is None:
            return None

        token_start = document.cursor_position - len(prefix)
        text = document.text

        if (
            token_start < 0
            or token_start >= len(text)
            or text[token_start] not in SKILL_SIGILS
        ):
            return None

        token_end = token_start + 1
        while token_end < len(text):
            char = text[token_end]
            allowed = "_.-"
            if text[token_start] == "@":
                allowed += "/\\"
            if not (char.isalnum() or char in allowed):
                break
            token_end += 1

        token = text[token_start:token_end]
        if not token:
            return None
        return token_start, token

    @staticmethod
    def _delete_to_line_start(buffer) -> str:
        """删除当前逻辑行中光标之前的文本并返回被删内容。"""
        count = len(buffer.document.current_line_before_cursor)
        if count:
            return buffer.delete_before_cursor(count=count)
        if buffer.cursor_position > 0:
            return buffer.delete_before_cursor(count=1)
        return ""

    @staticmethod
    def _history_cursor_at_boundary(buffer) -> bool:
        """判断光标是否位于整个输入文本的首尾。"""
        return buffer.cursor_position in (0, len(buffer.text))

    @staticmethod
    def apply_completion(buffer, completion: Completion) -> None:
        """应用补全，并让斜杠命令替换光标后的剩余输入。"""
        state = buffer.complete_state
        document = state.original_document if state is not None else buffer.document
        cursor = document.cursor_position
        start = cursor + completion.start_position

        command = (
            slash_command_query(document) is not None
            or document.text_before_cursor.lstrip().startswith("/")
        )

        suffix = "" if command else document.text_after_cursor
        text = document.text[:start] + completion.text + suffix

        buffer.complete_state = None

        buffer.document = Document(
            text,
            cursor_position=start + len(completion.text),
        )

    def _token_menu_match_indices(
        self,
        document: Document,
        completion: Completion,
    ) -> tuple[int, ...] | None:
        """返回补全候选中需要高亮的位置。"""
        query = skill_query_token(document.text_before_cursor)
        category = file_category(completion.display_meta_text)
        if query and query.startswith("@") and category in {"File", "Dir"}:
            return self.file_search.match_indices(
                completion.text.strip(),
                completion.display_text,
                query[1:].strip(),
            )

        if completion.text[:1] in SKILL_SIGILS:
            if query is None:
                return None
            return subsequence_match_indices(
                completion.display_text,
                query[1:].strip(),
            )

        if completion.text.startswith("/"):
            query = slash_command_query(document)
            if query is None:
                return None

            token = query.token[1:].strip()
            if not token:
                return None

            offset = 1 if completion.display_text.startswith("/") else 0
            return prefix_match_indices(
                completion.display_text,
                token,
                offset=offset,
            )

        return None

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

        if (
            completion.text[:1] in SKILL_SIGILS
            and completion.text != completion.text[:1]
        ):
            self.confirm_selected_skill(buffer)

        if buffer.document == original:
            if slash_command_query(buffer.document) is not None:
                self.refresh_completion_menu(buffer, selected_text=completion.text)
            else:
                self.dismiss_completion_menu(buffer)
        elif completion.text in SKILL_SIGILS:
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
        elif slash_command_query(buffer.document) is not None:
            # 让已接受的前缀命令与精确命令继续使用同一个原生菜单。
            # 上面的参数命令已经插入分隔空格，因此不会进入此分支。
            self.refresh_completion_menu(buffer, selected_text=completion.text)

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
        command = remainder.lstrip(" ")
        removed = 1 + len(remainder) - len(command)

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
        previous_text: str
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
        previous_text: str
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

        self.notify_input_layout()

    def _slash_completion_menu_dismissed(self, document: Document) -> bool:
        """判断当前斜杠命令 token 是否已主动关闭补全。"""
        if slash_command_query(document) is None:
            return False
        token = slash_command_dismissal_token(document)
        return (
            token is not None
            and token == self._token_menu_state.command_dismissal_token()
        )

    def _skill_completion_menu_dismissed(self, document: Document) -> bool:
        """判断当前 skill token 是否已主动关闭补全。"""
        token = self._skill_completion_token(document)
        dismissed = self._token_menu_state.dismissed_skill()

        return (
            token is not None
            and dismissed is not None
            and dismissed.matches(document.text, token[1], token[0])
        )

    def _sync_mention_popup_lifecycle(self, document: Document) -> None:
        """同步 `@` popup 生命周期，并在新实例上重启文件搜索。"""
        query = skill_query_token(document.text_before_cursor)
        mention_query = bool(query and query.startswith("@"))
        popup_available = bool(
            mention_query
            and not self._skill_completion_menu_dismissed(document)
            and not self._committed_skill_completion_dismissed(document)
        )

        if not popup_available:
            was_active = self._mention_popup_active
            self._mention_popup_active = False
            if not mention_query:
                self._skill_search_mode_index = 0
                if was_active:
                    self.file_search.cancel()
            return None

        if self._mention_popup_active:
            return None

        # 新 popup 不继承旧查询快照或筛选模式。
        self._mention_popup_active = True
        self._skill_search_mode_index = 0
        self.file_search.cancel()

    def _update_committed_skill(self, text: str) -> None:
        """根据文本变化平移或撤销已确认的 skill 查询锚点。"""
        committed = self._token_menu_state.committed_skill()
        if committed is None or committed.document_text == text:
            return None

        previous: str = committed.document_text
        prefix: int = 0
        prefix_limit: int = min(len(previous), len(text))

        while prefix < prefix_limit and previous[prefix] == text[prefix]:
            prefix += 1

        suffix: int = 0
        suffix_limit: int = min(len(previous) - prefix, len(text) - prefix)

        while (
            suffix < suffix_limit
            and previous[len(previous) - suffix - 1]
            == text[len(text) - suffix - 1]
        ):
            suffix += 1

        previous_change_end = len(previous) - suffix
        current_change_end = len(text) - suffix

        if previous_change_end <= committed.start:
            start = committed.start + current_change_end - previous_change_end
        elif prefix > committed.start:
            start = committed.start
        else:
            self._token_menu_state.set_committed_skill(None)
            return None

        if start < 0 or start >= len(text) or text[start] not in SKILL_SIGILS:
            self._token_menu_state.set_committed_skill(None)
            return None

        self._token_menu_state.set_committed_skill(CommittedTokenQuery(
            start=start,
            document_text=text,
        ))

    def _committed_skill_completion_dismissed(self, document: Document) -> bool:
        """判断当前查询是否属于已确认的 skill 补全会话。"""
        committed = self._token_menu_state.committed_skill()
        if committed is None or committed.document_text != document.text:
            return False

        query = skill_query_token(document.text_before_cursor)
        if query is None:
            return False

        query_start = document.cursor_position - len(query)
        return query_start == committed.start

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

    def _store_kill(self, text: str) -> None:
        """保存删除文本及其仍可恢复的折叠粘贴事实。"""
        if not text:
            return None
        self._kill_buffer = _TuiKillBuffer(
            text=text,
            paste_items=tuple(self.submission_state(text).items()),
        )

    def _yank_kill_buffer(self, buffer) -> None:
        """粘回 kill buffer，并为冲突的折叠粘贴分配新占位符。"""
        killed = self._kill_buffer
        if not killed.text:
            return None

        text = killed.text
        self.paste_store = self.submission_state(buffer.text)
        for placeholder, original in killed.paste_items:
            occurrences = text.count(placeholder)
            if not occurrences:
                continue
            if occurrences == 1 and placeholder not in buffer.text:
                self.paste_store[placeholder] = original
                continue
            for _index in range(occurrences):
                replacement = self._display_paste(
                    original,
                    f"{buffer.text}{text}",
                )
                text = text.replace(placeholder, replacement, 1)

        buffer.insert_text(text)
        self.notify_input_layout()

    def _reset_history_navigation(self) -> None:
        """重置输入历史导航状态。"""
        self._history_entries = ()
        self._history_index = None
        self._history_completion_dismissed = False

    @property
    def history_search_active(self) -> bool:
        """返回 footer 是否正在独占历史搜索输入。"""
        return self._history_search is not None

    def history_search_snapshot(self) -> TuiHistorySearchSnapshot | None:
        """返回当前历史搜索的只读展示快照。"""
        search = self._history_search
        if search is None:
            return None
        if not search.query:
            status: typing.Literal["idle", "match", "no_match"] = "idle"
        elif search.match_index is not None:
            status = "match"
        else:
            status = "no_match"
        return TuiHistorySearchSnapshot(query=search.query, status=status)

    def begin_history_search(self, buffer) -> None:
        """冻结当前草稿并让 footer 接管历史搜索输入。"""
        if self._history_search is not None:
            self.step_history_search(buffer, older=True)
            return None
        buffer.cancel_completion()
        self._reset_history_navigation()
        self._history_search = _TuiHistorySearchSession(
            original_document=buffer.document,
            original_paste_items=tuple(self.paste_store.items()),
            original_shell_mode=self.shell_mode,
        )
        self.notify_input_layout()

    def cancel_history_search(self, buffer) -> bool:
        """取消历史搜索并原样恢复进入搜索前的草稿。"""
        search = self._history_search
        if search is None:
            return False
        self._history_search = None
        self.set_shell_mode(search.original_shell_mode)
        self.restore_submission_state(dict(search.original_paste_items))
        buffer.document = search.original_document
        self._reset_history_navigation()
        self.notify_input_layout()
        return True

    def accept_history_search(self, buffer) -> bool:
        """把当前匹配接受为可编辑草稿，但不提交。"""
        search = self._history_search
        if search is None or search.match_index is None:
            return False
        self._history_search = None
        buffer.cursor_position = len(buffer.text)
        self._reset_history_navigation()
        self.notify_input_layout()
        return True

    def append_history_search_text(self, buffer, text: str) -> None:
        """向 footer 查询追加可打印文本并从最新记录重新搜索。"""
        search = self._history_search
        if search is None:
            return None
        search.query += text
        self._restart_history_search(buffer)

    def backspace_history_search(self, buffer) -> None:
        """删除 footer 查询的最后一个字符并重新搜索。"""
        search = self._history_search
        if search is None:
            return None
        search.query = search.query[:-1]
        self._restart_history_search(buffer)

    def clear_history_search(self, buffer) -> None:
        """清空 footer 查询并恢复原始草稿。"""
        search = self._history_search
        if search is None:
            return None
        search.query = ""
        self._restart_history_search(buffer)

    def step_history_search(self, buffer, *, older: bool) -> None:
        """在当前查询的唯一匹配项之间移动。"""
        search = self._history_search
        if search is None or not search.query:
            return None
        if not search.matches:
            self._restart_history_search(buffer)
            return None
        current = search.match_index if search.match_index is not None else 0
        target = min(
            len(search.matches) - 1,
            current + 1,
        ) if older else max(0, current - 1)
        search.match_index = target
        self._apply_history_search_entry(buffer, search.matches[target])

    def _restart_history_search(self, buffer) -> None:
        """按当前查询重建去重后的从新到旧匹配序列。"""
        search = self._history_search
        if search is None:
            return None
        query = search.query.casefold()
        if not query:
            search.matches = ()
            search.match_index = None
            self._restore_history_search_original(buffer, search)
            return None

        matches: list[TuiInputHistoryEntry] = []
        seen: set[TuiInputHistoryEntry] = set()
        for entry in reversed(self.history.entries()):
            if entry in seen or query not in entry.visible_text.casefold():
                continue
            seen.add(entry)
            matches.append(entry)
        search.matches = tuple(matches)
        search.match_index = 0 if matches else None
        if matches:
            self._apply_history_search_entry(buffer, matches[0])
        else:
            self._restore_history_search_original(buffer, search)

    def _restore_history_search_original(
        self,
        buffer,
        search: _TuiHistorySearchSession,
    ) -> None:
        """恢复搜索会话冻结的原始编辑状态。"""
        self.set_shell_mode(search.original_shell_mode)
        self.restore_submission_state(dict(search.original_paste_items))
        buffer.document = search.original_document
        self.dismiss_completion_menu(buffer)
        self.notify_input_layout()

    def _apply_history_search_entry(
        self,
        buffer,
        entry: TuiInputHistoryEntry,
    ) -> None:
        """把匹配历史项投影为输入区预览。"""
        self.set_shell_mode(entry.shell_mode)
        self.restore_submission_state(entry.paste_store)
        buffer.document = Document(
            entry.editable_text,
            cursor_position=len(entry.editable_text),
        )
        self.dismiss_completion_menu(buffer)
        self.notify_input_layout()

    def _start_history_navigation(self) -> None:
        """开始遍历完整输入历史。"""
        self._history_entries = self.history.entries()

        self._history_index = len(self._history_entries)

    def _history_navigation_matches_buffer(self, buffer) -> bool:
        """判断输入框是否仍处于当前输入历史位置。"""
        index = self._history_index
        if index is None:
            return False
        if index >= len(self._history_entries):
            return False

        entry = self._history_entries[index]

        text, shell_mode = self._history_entry_state(entry)

        return bool(
            buffer.text == text
            and self.shell_mode == shell_mode
            and self.paste_store == entry.paste_store
        )

    def _recalled_history_entry_at_boundary(self, buffer) -> bool:
        """判断当前输入是否仍是历史项且光标位于首尾。"""
        index = self._history_index
        return bool(
            index is not None
            and index < len(self._history_entries)
            and buffer.cursor_position in (0, len(buffer.text))
            and self._history_navigation_matches_buffer(buffer)
        )

    def _navigate_history(self, buffer, *, step: int, count: int) -> None:
        """在输入历史项之间导航。"""
        if not self._history_navigation_allowed(buffer):
            return None

        if not self._history_navigation_matches_buffer(buffer):
            if self._history_index is None:
                self._start_history_navigation()
            elif buffer.text:
                return None

        index = self._history_index

        if index is None:
            return None

        target = min(
            len(self._history_entries),
            max(0, index + step * max(1, count)),
        )
        if target == index:
            return None

        self._history_index = target

        if target == len(self._history_entries):
            self.set_shell_mode(False)
            self.restore_submission_state({})
            buffer.document = Document("", cursor_position=0)
            self._token_menu_state.clear_command_dismissal()
            self.dismiss_completion_menu(buffer)
            self._reset_history_navigation()
            self.notify_input_layout()
            return None

        entry = self._history_entries[target]

        text, shell_mode = self._history_entry_state(entry)

        self.set_shell_mode(shell_mode)
        self.restore_submission_state(entry.paste_store)

        buffer.document = Document(text, cursor_position=len(text))
        self.dismiss_completion_menu(buffer)
        self._history_completion_dismissed = (
            slash_command_query(buffer.document) is not None
        )
        self.notify_input_layout()

    def _history_navigation_allowed(self, buffer) -> bool:
        """判断当前输入是否允许继续历史导航。"""
        if not self._history_cursor_at_boundary(buffer):
            return False

        if not buffer.text:
            return bool(self.history.entries())

        index = self._history_index
        if index is None or index >= len(self._history_entries):
            return False

        return self._history_navigation_matches_buffer(buffer)

    def _build_key_bindings(self) -> KeyBindings:
        """创建 TUI 输入区按键绑定。"""
        bindings = KeyBindings()
        input_focused = has_focus(INPUT_BUFFER_NAME)
        history_search_active = input_focused & Condition(
            lambda: self.history_search_active
        )
        ordinary_input = input_focused & ~history_search_active

        @bind_key_action(
            bindings,
            self.keymap.editor.interrupt,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            self.handle_interrupt(event.app.current_buffer)

        direct_exit = ordinary_input & Condition(
            lambda: bool(
                self.can_exit()
                and not get_app().current_buffer.text
                and get_app().current_buffer.complete_state is None
                and not self.shell_mode
            )
        )

        @bind_key_action(
            bindings,
            self.keymap.editor.exit,
            eager=True,
            binding_filter=direct_exit,
        )
        def _(event) -> None:
            _ = event
            self.exit_handler()

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_line,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            buffer.cancel_completion()
            deleted = self._delete_to_line_start(buffer)
            if deleted:
                self._store_kill(deleted)
            self.paste_store = self.submission_state(buffer.text)

            if not buffer.text:
                self.set_shell_mode(False)

            if deleted:
                self._finish_destructive_edit(
                    buffer,
                    previous_text=previous_text,
                )
            else:
                self.notify_input_layout()

        shell_mode_empty = ordinary_input & Condition(
            lambda: self.shell_mode and not get_app().current_buffer.text
        )

        @bind_key_action(
            bindings,
            self.keymap.editor.cancel_shell_mode,
            eager=True,
            binding_filter=shell_mode_empty,
        )
        def _(event) -> None:
            self.set_shell_mode(False)
            event.app.invalidate()

        completion_menu_open = ordinary_input & Condition(
            lambda: bool(
                self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is not None
            )
        )
        @bind_key_action(
            bindings,
            self.keymap.editor.cancel_completion,
            eager=True,
            binding_filter=completion_menu_open,
        )
        def _(event) -> None:
            self.dismiss_completion_menu(event.app.current_buffer)
            event.app.invalidate()

        turn_interrupt = ordinary_input & Condition(
            lambda: bool(
                self.can_interrupt_turn()
                and get_app().current_buffer.complete_state is None
                and self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is None
                and not (
                    self.shell_mode
                    and not get_app().current_buffer.text
                )
            )
        )
        standalone_key = Condition(
            lambda: not get_app().key_processor.input_queue
        )

        @bind_key_action(
            bindings,
            self.keymap.chat.interrupt_turn,
            eager=standalone_key,
            binding_filter=turn_interrupt,
        )
        def _(event) -> None:
            _ = event
            self.turn_interrupt_handler()
            self.notify_input_layout()

        edit_backspace = ordinary_input & ~shell_mode_empty

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_backward,
            eager=True,
            binding_filter=edit_backspace,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            state = buffer.complete_state
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

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_forward,
            eager=True,
            binding_filter=ordinary_input & ~direct_exit,
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

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_word_backward,
            eager=True,
            binding_filter=ordinary_input,
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

            removed_count = len(previous_text) - len(buffer.text)
            if removed_count > 0:
                self._store_kill(previous_text[
                    buffer.cursor_position:
                    buffer.cursor_position + removed_count
                ])

            self._finish_destructive_edit(
                buffer,
                previous_text=previous_text,
            )

        @bind_key_action(
            bindings,
            self.keymap.editor.undo,
            eager=True,
            binding_filter=ordinary_input,
            save_before=lambda event: False,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            buffer.undo()
            self._restore_shell_mode_after_undo(
                buffer,
                previous_text=previous_text,
            )
            self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.editor.move_left,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self._skill_mention_popup_open(buffer):
                self.cycle_skill_search_mode(-1)
                return None
            self._move_cursor_with_completion_menu(
                buffer,
                move_cursor=buffer.cursor_left,
                count=max(1, event.arg),
            )

        @bind_key_action(
            bindings,
            self.keymap.editor.move_right,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self._skill_mention_popup_open(buffer):
                self.cycle_skill_search_mode(1)
                return None
            self._move_cursor_with_completion_menu(
                buffer,
                move_cursor=buffer.cursor_right,
                count=max(1, event.arg),
            )

        @bind_key_action(
            bindings,
            self.keymap.editor.move_line_start,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            offset = buffer.document.get_start_of_line_position()
            key_sequence = event.key_sequence
            pressed_key = key_sequence[-1].key if key_sequence else None
            if (
                not offset
                and pressed_key == Keys.ControlA
                and buffer.cursor_position > 0
            ):
                previous_character = buffer.cursor_position - 1
                buffer.cursor_position = (
                    buffer.text.rfind("\n", 0, previous_character) + 1
                )
            elif offset:
                buffer.cursor_position += offset
            else:
                return None
            self.dismiss_completion_menu(buffer)
            self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.editor.move_line_end,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            offset = buffer.document.get_end_of_line_position()
            key_sequence = event.key_sequence
            pressed_key = key_sequence[-1].key if key_sequence else None
            if (
                not offset
                and pressed_key == Keys.ControlE
                and buffer.cursor_position < len(buffer.text)
            ):
                next_line_start = buffer.cursor_position + 1
                next_line_end = buffer.text.find("\n", next_line_start)
                buffer.cursor_position = (
                    len(buffer.text)
                    if next_line_end < 0
                    else next_line_end
                )
            elif offset:
                buffer.cursor_position += offset
            else:
                return None
            self.dismiss_completion_menu(buffer)
            self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.editor.move_word_left,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            offset = buffer.document.find_previous_word_beginning(
                count=max(1, event.arg),
            )
            if offset is not None:
                buffer.cursor_position += offset
                self.dismiss_completion_menu(buffer)
                self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.editor.move_word_right,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            offset = buffer.document.find_next_word_ending(
                count=max(1, event.arg),
            )
            if offset is not None:
                buffer.cursor_position += offset
                self.dismiss_completion_menu(buffer)
                self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_word_forward,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            offset = buffer.document.find_next_word_ending(
                count=max(1, event.arg),
            )
            deleted = buffer.delete(count=offset or 0)
            if deleted:
                self._store_kill(deleted)
                self._finish_destructive_edit(
                    buffer,
                    previous_text=previous_text,
                )

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_to_line_end,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            previous_text = buffer.text
            count = len(buffer.document.current_line_after_cursor)
            if count:
                deleted = buffer.delete(count=count)
            elif buffer.cursor_position < len(buffer.text):
                deleted = buffer.delete(count=1)
            else:
                deleted = ""
            if deleted:
                self._store_kill(deleted)
                self._finish_destructive_edit(
                    buffer,
                    previous_text=previous_text,
                )

        @bind_key_action(
            bindings,
            self.keymap.editor.yank,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            self._yank_kill_buffer(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.editor.insert_newline,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            completion_open = bool(
                buffer.complete_state is not None
                or self.completion_menu_completions(buffer.document) is not None
            )
            buffer.insert_text("\n")
            if (
                completion_open
                and self.completion_menu_completions(buffer.document) is None
            ):
                buffer.cancel_completion()
                self.notify_input_layout()

        @bind_key_action(
            bindings,
            self.keymap.global_keys.copy_last_response,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(_event) -> None:
            self.copy_last_response_handler()

        queue_rollback = ordinary_input & Condition(
            lambda: bool(
                self.can_rollback_queue()
                and self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is None
            )
        )

        queue_rollback_reserved = ordinary_input & Condition(
            lambda: bool(
                self.can_submit_queue()
                and not self.can_rollback_queue()
                and self.completion_menu_completions(
                    get_app().current_buffer.document
                ) is None
            )
        )

        history_backtrack = ordinary_input & Condition(
            lambda: bool(self.can_backtrack_history())
        )
        missing_backtrack = ordinary_input & Condition(
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

        @bind_key_action(
            bindings,
            self.keymap.chat.edit_queued_message,
            eager=True,
            binding_filter=queue_rollback,
        )
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()
            self.rollback_queue_handler()

        @bind_key_action(
            bindings,
            self.keymap.chat.edit_queued_message[:1],
            eager=True,
            binding_filter=queue_rollback_reserved,
        )
        def _(event) -> None:
            event.app.current_buffer.cancel_completion()

        @bind_key_action(
            bindings,
            self.keymap.composer.enter_shell_mode,
            eager=True,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if not self.shell_mode and not buffer.text:
                buffer.cancel_completion()
                self.set_shell_mode(True)
                event.app.invalidate()
                return None
            buffer.insert_text("!")

        shortcut_help_available = ordinary_input & Condition(
            lambda: bool(
                not get_app().current_buffer.text
                and get_app().current_buffer.complete_state is None
                and not self.shell_mode
            )
        )

        @bind_key_action(
            bindings,
            self.keymap.composer.toggle_shortcuts,
            eager=True,
            binding_filter=shortcut_help_available,
        )
        def _(_event) -> None:
            self.shortcut_help_handler()

        @bind_key_action(
            bindings,
            self.keymap.composer.queue,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer

            menu_completion = self._selected_menu_completion(buffer)

            completion_menu_opened = bool(
                self.completion_menu_completions(buffer.document)
            )

            if menu_completion is not None:
                slash_query = slash_command_query(buffer.document)
                slash_command_complete = (
                    slash_query is not None
                    and slash_query.token.casefold()
                    == menu_completion.display_text.casefold()
                )
                if (
                    slash_command_complete
                    and menu_completion.text not in self.TAB_DISPATCH_COMMANDS
                    and self.can_submit_queue()
                    and buffer.text.strip()
                ):
                    buffer.cancel_completion()
                    self.queue_submission_handler(buffer)
                    return None
                self._apply_menu_completion(buffer, menu_completion)
                if menu_completion.text in self.TAB_DISPATCH_COMMANDS:
                    buffer.validate_and_handle()
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
            elif buffer.text.strip():
                buffer.cancel_completion()
                buffer.validate_and_handle()
            else:
                buffer.start_completion(
                    select_first=True,
                    complete_event=CompleteEvent(completion_requested=True),
                )

        @bindings.add("/", eager=True, filter=completion_menu_open)
        def _(event) -> None:
            buffer = event.app.current_buffer
            completion = self._selected_menu_completion(buffer)
            if completion is not None and completion.text.startswith("/"):
                self._apply_menu_completion(buffer, completion)
                return None
            buffer.insert_text("/")

        @bind_key_action(
            bindings,
            self.keymap.composer.previous_completion,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            self._select_completion(
                event.app.current_buffer,
                -max(1, event.arg),
            )

        @bindings.add(
            Keys.BracketedPaste,
            eager=True,
            filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            data = sanitize_terminal_text(event.data or "")
            buffer.cancel_completion()
            if not buffer.text and data.startswith("!"):
                self.set_shell_mode(True)
                data = data[1:].lstrip(" ")
            buffer.insert_text(self._display_paste(data, buffer.text))

        @bind_key_action(
            bindings,
            self.keymap.composer.submit,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            completion = self._selected_menu_completion(buffer)
            if completion is not None:
                self._apply_menu_completion(buffer, completion)
                if (
                    completion.text in SKILL_SIGILS
                    or completion.text[:1] in SKILL_SIGILS
                    or file_category(completion.display_meta_text) in {
                    "File",
                    "Dir",
                }
                    or completion.text in self.PARAMETERIZED_COMMANDS
                ):
                    return

            else:
                state = buffer.complete_state
                if state is not None and state.current_completion is not None:
                    completion = state.current_completion
                    self.apply_completion(buffer, completion)
                    if completion.text[:1] in SKILL_SIGILS:
                        return

            buffer.validate_and_handle()

        @bind_key_action(
            bindings,
            self.keymap.editor.move_up,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer

            if self.completion_menu_completions(buffer.document):
                self._select_completion(buffer, -max(1, event.arg))
            elif buffer.complete_state:
                self._select_completion(buffer, -max(1, event.arg))
            elif buffer.document.cursor_position_row > 0:
                self._move_cursor_with_completion_menu(
                    buffer,
                    move_cursor=buffer.cursor_up,
                    count=max(1, event.arg),
                )
            elif (
                not buffer.selection_state
                and self._history_navigation_allowed(buffer)
            ):
                self._navigate_history(
                    buffer,
                    step=-1,
                    count=max(1, event.arg),
                )

        @bind_key_action(
            bindings,
            self.keymap.editor.move_down,
            binding_filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self.completion_menu_completions(buffer.document):
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.complete_state:
                self._select_completion(buffer, max(1, event.arg))
            elif buffer.document.cursor_position_row < buffer.document.line_count - 1:
                self._move_cursor_with_completion_menu(
                    buffer,
                    move_cursor=buffer.cursor_down,
                    count=max(1, event.arg),
                )
            elif (
                not buffer.selection_state
                and self._history_navigation_allowed(buffer)
            ):
                self._navigate_history(
                    buffer,
                    step=1,
                    count=max(1, event.arg),
                )

        @bindings.add(
            Keys.ControlUp,
            eager=True,
            filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self._history_navigation_allowed(buffer):
                self._navigate_history(
                    buffer,
                    step=-1,
                    count=max(1, event.arg),
                )

        @bindings.add(
            Keys.ControlDown,
            eager=True,
            filter=ordinary_input,
        )
        def _(event) -> None:
            buffer = event.app.current_buffer
            if self._history_navigation_allowed(buffer):
                self._navigate_history(
                    buffer,
                    step=1,
                    count=max(1, event.arg),
                )

        @bind_key_action(
            bindings,
            self.keymap.composer.history_search_previous,
            eager=True,
            binding_filter=input_focused,
        )
        def _(event) -> None:
            self.begin_history_search(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.composer.history_search_next,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.step_history_search(event.app.current_buffer, older=False)

        @bind_key_action(
            bindings,
            self.keymap.editor.interrupt,
            eager=True,
            binding_filter=history_search_active,
        )
        @bind_key_action(
            bindings,
            self.keymap.chat.interrupt_turn,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.cancel_history_search(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.composer.submit,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.accept_history_search(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_backward,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.backspace_history_search(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.editor.delete_line,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.clear_history_search(event.app.current_buffer)

        @bind_key_action(
            bindings,
            self.keymap.editor.move_up,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.step_history_search(event.app.current_buffer, older=True)

        @bind_key_action(
            bindings,
            self.keymap.editor.move_down,
            eager=True,
            binding_filter=history_search_active,
        )
        def _(event) -> None:
            self.step_history_search(event.app.current_buffer, older=False)

        @bindings.add(Keys.Any, eager=True, filter=history_search_active)
        def _(event) -> None:
            text = str(event.data or "")
            if text and text.isprintable():
                self.append_history_search_text(
                    event.app.current_buffer,
                    text,
                )

        return bindings

    def _move_cursor_with_completion_menu(
        self,
        buffer,
        *,
        move_cursor: typing.Callable[[int], None],
        count: int
    ) -> None:
        """移动光标并同步补全菜单。"""
        previous_position = buffer.cursor_position
        move_cursor(count)

        if buffer.cursor_position != previous_position:
            if (
                self._history_completion_dismissed
                and not self._recalled_history_entry_at_boundary(buffer)
            ):
                self._token_menu_state.clear_command_dismissal()
                self._history_completion_dismissed = False
            elif self._recalled_history_entry_at_boundary(buffer):
                slash_token = slash_command_dismissal_token(buffer.document)
                if (
                    slash_token is not None
                    and slash_command_query(buffer.document) is not None
                ):
                    if (
                        self._token_menu_state.command_dismissal_token()
                        != slash_token
                    ):
                        self._token_menu_state.dismiss_command(slash_token)
                        self._history_completion_dismissed = True
                    buffer.cancel_completion()
            self.sync_completion_menu(buffer)

    def _skill_mention_popup_open(self, buffer) -> bool:
        """判断当前是否正在展示 `@` skill popup。"""
        query = skill_query_token(buffer.document.text_before_cursor)
        return bool(
            query
            and query.startswith("@")
            and self.completion_menu_completions(buffer.document) is not None
        )

    def new_placeholder(self) -> str:
        """为新的输入轮次生成一次占位文案。"""
        prompt = random.choice(self.PLACEHOLDER_PROMPTS)
        command = random.choice(self.PLACEHOLDER_COMMANDS)

        return f"{prompt}, {command}"

    def token_menu_snapshot(self, buffer) -> TokenMenuSnapshot | None:
        """返回当前输入 token 菜单快照。"""
        state = getattr(buffer, "complete_state", None)
        if state is None or not state.completions:
            return None

        selected = state.complete_index if state.complete_index is not None else 0
        return TokenMenuSnapshot(
            items=tuple(
                TokenMenuItem(
                    display_text=completion.display_text,
                    meta_text=completion.display_meta_text,
                    kind=self._token_menu_kind(buffer.document, completion),
                    match_indices=self._token_menu_match_indices(
                        buffer.document,
                        completion,
                    ),
                )
                for completion in state.completions
            ),
            selected=selected,
        )

    def set_skills(self, skills: typing.Iterable[SkillSpec]) -> None:
        """更新输入补全和高亮使用的 skill 快照。"""
        self.skills = tuple(skills)

        app = get_app_or_none()
        if app is None:
            return None

        buffer = app.current_buffer
        if getattr(buffer, "name", None) != INPUT_BUFFER_NAME:
            return None

        self.reopen_completion_menu(buffer)
        self.sync_completion_menu(buffer)
        self.notify_input_layout()

    def set_workspace_root(self, workspace_root: Path | str) -> None:
        """更新 `@` 文件搜索使用的工作区根目录。"""
        resolved = Path(workspace_root).expanduser().resolve()
        if resolved == self.workspace_root:
            return None
        self.file_search.cancel()
        self.workspace_root = resolved

        app = get_app_or_none()
        if app is None:
            return None
        buffer = app.current_buffer
        if getattr(buffer, "name", None) != INPUT_BUFFER_NAME:
            return None
        self.sync_completion_menu(buffer)
        self.notify_input_layout()

    def cycle_skill_search_mode(self, step: int) -> None:
        """循环切换 `@` popup 搜索模式并刷新布局。"""
        self._skill_search_mode_index = (
                                            self._skill_search_mode_index + (1 if step >= 0 else -1)
                                        ) % len(SKILL_SEARCH_MODES)
        app = get_app_or_none()
        if app is not None:
            buffer = app.current_buffer
            if getattr(buffer, "name", None) == INPUT_BUFFER_NAME:
                self.refresh_completion_menu(buffer)
        self.notify_input_layout()

    def set_shell_mode(self, active: bool) -> None:
        """更新输入框的 Shell 前缀模式。"""
        self.shell_mode = bool(active)
        self.auto_suggest.shell_mode = self.shell_mode
        self._shell_mode_undo_transition = None

    def bind_interrupt(
        self,
        handler: typing.Callable[[], InterruptDisposition]
    ) -> None:
        """绑定主运行时提供的输入中断处理函数。"""
        self.interrupt_handler = handler

    def bind_turn_interrupt(
        self,
        can_interrupt: typing.Callable[[], bool],
        handler: typing.Callable[[], InterruptDisposition],
    ) -> None:
        """绑定 Esc 中断当前轮次的独立按键动作。"""
        self.can_interrupt_turn = can_interrupt
        self.turn_interrupt_handler = handler

    def bind_copy_last_response(self, handler: typing.Callable[[], None]) -> None:
        """绑定 Ctrl+O 直接复制最近整体回复的本地动作。"""
        self.copy_last_response_handler = handler

    def bind_shortcut_help(self, handler: typing.Callable[[], None]) -> None:
        """绑定空草稿问号触发的只读快捷键页面。"""
        self.shortcut_help_handler = handler

    def bind_input_layout(self, handler: typing.Callable[[], None]) -> None:
        """绑定输入内容变化后的当前帧布局刷新动作。"""
        self._input_layout_handler = handler

    def bind_input_activity(self, handler: typing.Callable[[], None]) -> None:
        """绑定普通按键完成后的临时交互状态清理动作。"""
        self._input_activity_handler = handler

    def begin_key_dispatch(self) -> None:
        """开始一次按键分派并清空当前按键的中断身份。"""
        self._interrupt_dispatched = False

    def finish_key_dispatch(self) -> None:
        """结束按键分派，并让非 Ctrl+C 按键终止退出确认。"""
        if not self._interrupt_dispatched:
            self._input_activity_handler()
        self._interrupt_dispatched = False

    def bind_file_search_refresh(self, handler: typing.Callable[[], None]) -> None:
        """绑定后台文件搜索结果到达后的线程安全刷新动作。"""
        self.file_search.bind_refresh(handler)

    def close_file_search(self) -> None:
        """关闭当前后台文件搜索会话。"""
        self.file_search.close()

    def completion_empty_message(self, document: Document) -> str:
        """返回当前补全空状态应展示的文字。"""
        query = skill_query_token(document.text_before_cursor)
        if query and query.startswith("@"):
            return self.file_search.empty_message(document.text_before_cursor)
        return "no matches"

    def notify_input_layout(self) -> None:
        """通知布局层按当前输入和补全状态刷新画面。"""
        self._input_layout_handler()

    def handle_interrupt(self, buffer) -> InterruptDisposition:
        """把 Ctrl+C 交给主运行时统一清理草稿或中断任务。"""
        self._interrupt_dispatched = True
        disposition = self.interrupt_handler()
        self.notify_input_layout()
        return disposition

    def completion_menu_completions(self, document: Document) -> tuple[Completion, ...] | None:
        """返回当前未被关闭的命令或 skill 菜单项。"""
        self._sync_mention_popup_lifecycle(document)
        if (
            self._slash_completion_menu_dismissed(document)
            or self._skill_completion_menu_dismissed(document)
            or self._committed_skill_completion_dismissed(document)
        ):
            return None
        completions = self.completer.menu_completions(document)
        if completions is None:
            return None

        query = skill_query_token(document.text_before_cursor)
        if not query or not query.startswith("@"):
            return completions

        if self.skill_search_mode == "Filesystem Only":
            return tuple(
                completion
                for completion in completions
                if file_category(completion.display_meta_text) in {"File", "Dir"}
            )

        if self.skill_search_mode == "Plugins":
            return tuple(
                completion
                for completion in completions
                if file_category(completion.display_meta_text) is None
            )

        return completions

    def completion_menu_has_skill_items(self, document: Document) -> bool:
        """判断当前补全菜单是否包含 skill 候选。"""
        completions = self.completion_menu_completions(document)
        return bool(
            completions
            and any(
                completion.text[:1] in SKILL_SIGILS
                for completion in completions
            )
        )

    def reopen_completion_menu(self, buffer) -> None:
        """在输入内容变化后允许补全菜单重新显示。"""
        history_dismissed = self._history_completion_dismissed
        self._history_completion_dismissed = False
        self._update_committed_skill(buffer.text)
        query = skill_query_token(buffer.document.text_before_cursor)
        if not query or not query.startswith("@"):
            self._mention_popup_active = False
            self._skill_search_mode_index = 0
            self.file_search.cancel()
        token = slash_command_dismissal_token(buffer.document)
        if history_dismissed:
            self._token_menu_state.clear_command_dismissal()
        elif token != self._token_menu_state.command_dismissal_token():
            self._token_menu_state.clear_command_dismissal()

    def confirm_selected_skill(self, buffer) -> None:
        """确认光标前最后一个已知 skill 查询锚点。"""
        document = buffer.document

        committed: CommittedTokenQuery | None = None

        for start, end, _name in iter_known_skill_tokens(
            document.text,
            skills=self.skills,
        ):
            if end > document.cursor_position:
                break
            committed = CommittedTokenQuery(
                start=start,
                document_text=document.text,
            )

        self._token_menu_state.set_committed_skill(committed)

    def clear_selected_skill(self) -> None:
        """清除已确认的 skill 查询状态。"""
        self._token_menu_state.set_committed_skill(None)

    def dismiss_completion_menu(self, buffer) -> None:
        """关闭当前补全菜单并保留输入内容。"""
        self._history_completion_dismissed = False
        document = getattr(buffer, "document", None)
        if document is not None:
            slash_token = slash_command_dismissal_token(document)
            if slash_token is not None and slash_command_query(document) is not None:
                self._token_menu_state.dismiss_command(slash_token)
            else:
                skill_token = self._skill_completion_token(document)
                if skill_token is not None:
                    token_start, token = skill_token
                    self._token_menu_state.dismiss_skill(DismissedToken.capture(
                        document.text,
                        token,
                        token_start,
                    ))
                    if token.startswith("@"):
                        self._mention_popup_active = False
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
            buffer.cancel_completion()
            return None

        completions = self.completion_menu_completions(buffer.document)
        if not completions:
            buffer.cancel_completion()
            return None
        if (
            len(completions) == 1
            and slash_command_query(buffer.document) is None
        ):
            completions = tuple(
                completion
                for completion in completions
                if completion_changes_input(buffer.document, completion)
            )
        if not completions:
            buffer.cancel_completion()
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

    def refresh_inserted_completion_menu(
        self,
        buffer
    ) -> None:
        """在字符插入后同步刷新可用的补全菜单。"""
        self.sync_completion_menu(buffer)

    def sync_completion_menu(
        self,
        buffer,
        selected_text: str | None = None
    ) -> None:
        """按当前光标位置同步补全菜单。"""
        if not buffer.completer or not buffer.complete_while_typing():
            return None

        if selected_text is None:
            state = getattr(buffer, "complete_state", None)

            selected_text = (
                state.current_completion.text
                if state is not None and state.current_completion is not None
                else None
            )

        self.refresh_completion_menu(buffer, selected_text)

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
        self.can_rollback_queue = can_rollback
        self.rollback_queue_handler = handler

    def bind_queue_submission(
        self,
        can_submit: typing.Callable[[], bool],
        handler: typing.Callable[[typing.Any], None]
    ) -> None:
        """绑定执行期使用 Tab 提交待处理消息的可用状态。"""
        self.can_submit_queue = can_submit
        self.queue_submission_handler = handler

    def bind_history_backtrack(
        self,
        can_backtrack: typing.Callable[[], bool],
        handler: typing.Callable[[], None],
        can_report_missing: typing.Callable[[], bool],
        missing_handler: typing.Callable[[], None]
    ) -> None:
        """绑定空输入状态下的历史编辑入口。"""
        self.can_backtrack_history = can_backtrack
        self.backtrack_history_handler = handler
        self.can_report_missing_backtrack = can_report_missing
        self.missing_backtrack_handler = missing_handler

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

    def rollback_submission_history(
        self,
        text: str,
        *,
        alternate_text: str | None = None,
    ) -> None:
        """撤销最近一次匹配的输入历史提交。"""
        self.history.rollback_latest(text, alternate_text=alternate_text)

    def record_submission_history(
        self,
        editable_text: str,
        paste_store: dict[str, str],
        *,
        value: str,
        shell_mode: bool
    ) -> bool:
        """按提交文本和原始编辑意图记录输入历史。"""
        original_text = str(editable_text or "")
        submitted_text = str(value or "").strip()

        preserve_literal_paste = bool(
            not shell_mode
            and paste_store
            and not original_text.lstrip().startswith("!")
            and submitted_text.lstrip().startswith("!")
        )

        if shell_mode:
            history_text = submitted_text[1:].lstrip()
            history_paste_store: dict[str, str] = {}
        elif preserve_literal_paste:
            history_text = original_text
            history_paste_store = dict(paste_store)
        else:
            history_text = submitted_text
            history_paste_store = {}

        recorded = self.history.append_submission(
            history_text,
            history_paste_store,
            shell_mode=shell_mode,
        )
        automatic_text = (
            f"! {original_text.strip()}"
            if shell_mode and original_text.strip()
            else "!"
            if shell_mode
            else original_text
        )
        if automatic_text:
            self.history._suppress_next_automatic_append(automatic_text)
        return recorded

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


if __name__ == '__main__':
    pass
