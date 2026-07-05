# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import html
import random
import typing
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import (
    AutoSuggest,
    Suggestion
)
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova import const
from mind_core.terminal_input import clear_pending_input
from .commands import SlashCommandCompleter
from .ghost import (
    apply_ghost_prompt,
    iter_ghost_templates
)
from .skills import SkillTokenLexer


class CommandAutoSuggest(AutoSuggest):
    """行内提示视图。"""

    SLASH_HINTS: dict[str, str] = {
        "/attach"  : " <path>",
        "/attach " : "<path>",
        "/detach"  : " <index-or-path>",
        "/detach " : "<index-or-path>",
        "/model"   : " <name>",
        "/model "  : "<name>"
    }

    def __init__(self) -> None:
        self.mode: RunMode = DEFAULT_RUN_MODE
        self.ghost_templates: tuple[tuple[str, str], ...] = iter_ghost_templates()

    def set_mode(self, mode: RunMode) -> None:
        """设置当前输入模式。"""
        self.mode = mode

    def get_suggestion(self, buffer, document):
        """根据当前输入上下文生成行内提示。"""
        if getattr(buffer, "complete_state", None) is not None:
            return None

        text = document.text_before_cursor

        current_line = text.splitlines()[-1] if text.splitlines() else text
        if text.endswith("\n"):
            current_line = ""
        if not current_line:
            return None

        if current_line.startswith("/"):
            if current_line in self.SLASH_HINTS:
                return Suggestion(self.SLASH_HINTS[current_line])
            return None

        candidate = apply_ghost_prompt(current_line, self.ghost_templates)
        if candidate != current_line:
            return Suggestion(candidate[len(current_line):])

        return None


class PromptToolkitBox(object):
    """交互输入视图。"""

    PARAMETERIZED_COMMANDS: tuple[str, ...] = (
        "/attach ", "/detach ", "/model "
    )
    SKILLS_COMMAND_TEXT: str = "$"

    MODEL_DISPLAY_MAX: int    = 24
    WORKSPACE_LABEL_MAX: int  = 36
    PASTE_CHAR_THRESHOLD: int = 1200
    PASTE_LINE_THRESHOLD: int = 20
    PROMPT_DOT: str           = "&#183;"

    PLACEHOLDER_PROMPTS: tuple[str, ...] = (
        "Ask anything",
        "Describe a goal",
        "Paste context",
        "Request a change",
        "Inspect something"
    )

    PLACEHOLDER_COMMANDS: tuple[str, ...] = (
        "/ opens commands",
        "$ opens skills",
        "/permissions changes access",
        "/resume restores a session",
        "/new starts fresh",
        "/attach adds files",
        "! opens local shell",
        "! <cmd> runs shell once"
    )

    def __init__(self) -> None:
        self.history: InMemoryHistory         = InMemoryHistory()
        self.completer: SlashCommandCompleter = SlashCommandCompleter()
        self.auto_suggest: CommandAutoSuggest = CommandAutoSuggest()

        self.lexer: SkillTokenLexer    = SkillTokenLexer()
        self.key_bindings: KeyBindings = self._build_key_bindings()

        self.session: typing.Optional[PromptSession[str]] = None
        self.paste_store: dict[str, str]                  = {}

        self.style: Style = Style.from_dict({
            "prompt": "bold #E2E5EA",
            "prompt.kicker"                           : "bold #7B838E",
            "prompt.model"                            : "bold #F3F5F8",
            "prompt.muted"                            : "bold #767D87",
            "prompt.access"                           : "bold #8FC7EA",
            "prompt.access.full"                      : "bold #D8B26E",
            "prompt.workspace"                        : "bold #8A929C",
            "placeholder"                             : "bold #727983",
            "auto-suggestion"                         : "#5A616A bg:#0A0D18",
            "skill-token"                             : "bold #8FD7FF",
            "shell-escape"                            : "bold #FF6B6B",
            "paste-placeholder"                       : "bold #D3C27C",
            "completion-menu"                         : "bg:#111315 #D8DCE2",
            "completion-menu.completion"              : "bg:#111315 bold #D6DBE2",
            "completion-menu.completion.current"      : "bg:#3B4148 bold #F4F7FA",
            "completion-menu.meta.completion"         : "bg:#111315 #7D858F",
            "completion-menu.meta.completion.current" : "bg:#3B4148 #D9E0E7",
            "scrollbar.background"                    : "bg:#111315",
            "scrollbar.button"                        : "bg:#666D76"
        })

    @classmethod
    def _should_fold_paste(cls, text: str) -> bool:
        """判断粘贴内容是否需要折叠展示。"""
        if len(text) >= cls.PASTE_CHAR_THRESHOLD:
            return True
        return len(text.splitlines()) >= cls.PASTE_LINE_THRESHOLD

    @staticmethod
    def _theme(mode: RunMode, placeholder: str = "") -> dict[str, str]:
        theme = {
            "chat": {
                "brand" : "#4F8FC8",
                "soft"  : "#2F6FAD",
                "label" : "Chat"
            },
            "fast": {
                "brand" : "#4FA37D",
                "soft"  : "#2E7D5B",
                "label" : "Fast"
            },
            "plan": {
                "brand" : "#866FD1",
                "soft"  : "#6B57B8",
                "label" : "Plan"
            },
            "xtra": {
                "brand" : "#2DAA9E",
                "soft"  : "#1E7F78",
                "label" : "Xtra"
            }
        }[mode]

        return {
            **theme,
            "placeholder": placeholder or PromptToolkitBox._placeholder_text(theme["label"])
        }

    @staticmethod
    def _placeholder_text(mode_label: str) -> str:
        prompt  = random.choice(PromptToolkitBox.PLACEHOLDER_PROMPTS)
        command = random.choice(PromptToolkitBox.PLACEHOLDER_COMMANDS)
        return f"{mode_label}, {prompt}, {command}"

    @staticmethod
    def _clip_model_name(model: str, limit: int) -> str:
        """展示名称裁剪。"""
        if len(model) <= limit:
            return model
        return model[: max(0, limit - 3)] + "..."

    @staticmethod
    def _clip_workspace_label(label: str, limit: int) -> str:
        """workspace 展示名按 Codex 风格裁剪，保留根前缀和末尾目录信息。"""
        if limit <= 0:
            return ""
        if len(label) <= limit:
            return label
        prefix, sep = PromptToolkitBox._workspace_label_root(label)
        if not sep:
            return label[-limit:]

        if limit <= len(prefix):
            return prefix[:limit]

        body = label[len(prefix):]
        return PromptToolkitBox._clip_workspace_path_body(
            prefix,
            sep,
            body,
            limit
        )

    @staticmethod
    def _workspace_label_root(label: str) -> tuple[str, str]:
        """返回 workspace label 的根前缀和路径分隔符。"""
        if label.startswith("~\\"):
            return "~\\", "\\"
        if label.startswith("~/"):
            return "~/", "/"
        if len(label) >= 3 and label[1] == ":" and label[2] in ("\\", "/"):
            return label[:3], label[2]
        if label.startswith("\\\\"):
            parts = label.split("\\")
            if len(parts) >= 4 and parts[2] and parts[3]:
                return f"\\\\{parts[2]}\\{parts[3]}\\", "\\"
            return "\\\\", "\\"
        if label.startswith("/"):
            return "/", "/"
        if "\\" in label:
            return "", "\\"
        if "/" in label:
            return "", "/"

        return "", ""

    @staticmethod
    def _clip_workspace_path_body(
        prefix: str,
        sep: str,
        body: str,
        limit: int
    ) -> str:
        """动态省略路径段；末段过长时才额外标记末段截断。"""
        available = limit - len(prefix)
        marker    = f"…{sep}"

        if available <= len(marker):
            return (prefix + marker)[:limit]

        segments = [segment for segment in body.split(sep) if segment]
        if not segments:
            return prefix + body[-available:]

        suffix = segments[-1]
        if len(marker) + len(suffix) > available:
            keep = max(0, available - len(marker))
            return prefix + marker + PromptToolkitBox._clip_workspace_segment(
                suffix,
                keep
            )

        first_kept = len(segments) - 1
        for idx in range(len(segments) - 2, -1, -1):
            segment   = segments[idx]
            candidate = f"{segment}{sep}{suffix}"

            if len(marker) + len(candidate) > available:
                break

            suffix     = candidate
            first_kept = idx

        if first_kept == 0:
            return prefix + suffix

        return prefix + marker + suffix

    @staticmethod
    def _clip_workspace_segment(segment: str, limit: int) -> str:
        """裁剪单个路径段，使用省略号标记段内前缀被截断。"""
        if limit <= 0:
            return ""
        if len(segment) <= limit:
            return segment
        if limit == 1:
            return "…"

        return "…" + segment[-(limit - 1):]

    @staticmethod
    def _render_message(
        model: str,
        th: dict[str, str],
        workspace_label: str = "",
        access_label: str = ""
    ) -> HTML:
        """输入头部渲染。"""
        safe_model = html.escape(
            PromptToolkitBox._clip_model_name(model or "-", PromptToolkitBox.MODEL_DISPLAY_MAX)
        )
        safe_access = html.escape(str(access_label or "").strip())

        safe_workspace = html.escape(
            PromptToolkitBox._clip_workspace_label(
                workspace_label or "",
                PromptToolkitBox.WORKSPACE_LABEL_MAX
            )
        )
        workspace_parts = (
            f"<prompt.kicker>{PromptToolkitBox.PROMPT_DOT}</prompt.kicker> "
            f"<prompt.workspace>{safe_workspace}</prompt.workspace> "
            if safe_workspace
            else ""
        )

        access_style = "prompt.access.full" if safe_access.lower() == "elevated" else "prompt.access"

        access_parts = (
            f"<prompt.kicker>{PromptToolkitBox.PROMPT_DOT}</prompt.kicker> "
            f"<{access_style}>{safe_access}</{access_style}> "
            if safe_access
            else ""
        )
        return HTML(
            f"<prompt>"
            f"<prompt.brand fg='{th['brand']}'>{html.escape(const.APP_DESC)}</prompt.brand> "
            f"<prompt.kicker>{PromptToolkitBox.PROMPT_DOT}</prompt.kicker> "
            f"<prompt.model fg='{th['soft']}'>{safe_model}</prompt.model> "
            f"{access_parts}"
            f"{workspace_parts}"
            f"\n"
            f"<prompt.kicker>></prompt.kicker> "
            f"</prompt>"
        )

    @staticmethod
    def _render_continuation() -> HTML:
        """续行前缀渲染。"""
        return HTML(
            f"<prompt.kicker>.</prompt.kicker> "
        )

    @staticmethod
    def _sync_completion_suggestion(buf) -> None:
        """同步当前补全项的预览提示。"""
        if buf.suggestion is not None:
            buf.suggestion = None
            buf.on_suggestion_set.fire()

    @staticmethod
    def _completion_navigation_index(complete_state, step: int) -> typing.Optional[int]:
        """返回补全菜单只移动高亮时的新索引。"""
        if complete_state is None or not complete_state.completions:
            return None

        count   = len(complete_state.completions)
        current = complete_state.complete_index

        if current is None:
            return 0 if step > 0 else count - 1

        return (current + step) % count

    def _select_completion(self, buf, step: int) -> bool:
        """只移动补全菜单高亮，不把补全文本预写入输入区。"""
        state = getattr(buf, "complete_state", None)

        index = self._completion_navigation_index(state, step)
        if index is None:
            return False

        state.go_to_index(index)
        buf.on_completions_changed.fire()
        self._sync_completion_suggestion(buf)
        return True

    def _paste_placeholder(self, text: str, *, current_text: str = "") -> str:
        """生成粘贴内容的可见占位文本。"""
        self._prune_paste_store(current_text)

        index  = len(self.paste_store) + 1
        suffix = "" if index == 1 else f" #{index}"

        return f"[Pasted Content {len(text)} chars]{suffix}"

    def _display_text_for_paste(self, text: str, *, current_text: str = "") -> str:
        """返回输入框中用于显示的粘贴文本。"""
        if not self._should_fold_paste(text):
            return text

        placeholder = self._paste_placeholder(text, current_text=current_text)
        self.paste_store[placeholder] = text

        return placeholder

    def _prune_paste_store(self, current_text: str) -> None:
        """移除当前输入框中已经不存在的粘贴占位文本。"""
        if not self.paste_store:
            return None
        self.paste_store = {
            placeholder: original
            for placeholder, original in self.paste_store.items()
            if placeholder in current_text
        }

    def _restore_pasted_content(self, text: str) -> str:
        """把仍然完整存在的粘贴占位文本还原为原始内容。"""
        restored = text
        for placeholder, original in sorted(
            self.paste_store.items(),
            key=lambda item: len(item[0]),
            reverse=True
        ):
            restored = restored.replace(placeholder, original)
        return restored

    def _build_key_bindings(self) -> KeyBindings:
        """按键绑定集合。"""
        kb = KeyBindings()

        @kb.add("c-u", eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            buf.cancel_completion()
            buf.text = ""
            buf.cursor_position = 0
            self._sync_completion_suggestion(buf)
            self.paste_store.clear()
            event.app.invalidate()

        @kb.add("c-z", eager=True, save_before=lambda event: False)
        def _(event) -> None:
            buf = event.app.current_buffer
            buf.cancel_completion()
            buf.undo()
            self._sync_completion_suggestion(buf)
            event.app.invalidate()

        @kb.add("escape", "enter")
        @kb.add("c-o")
        def _(event) -> None:
            event.app.current_buffer.insert_text("\n")

        @kb.add("/", eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            buf.insert_text("/")
            buf.start_completion(
                select_first=False,
                complete_event=CompleteEvent(text_inserted=True)
            )
            self._sync_completion_suggestion(buf)

        @kb.add("$", eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            buf.insert_text("$")
            buf.start_completion(
                select_first=False,
                complete_event=CompleteEvent(text_inserted=True)
            )
            self._sync_completion_suggestion(buf)

        @kb.add("tab")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.suggestion and buf.suggestion.text:
                buf.insert_text(buf.suggestion.text)
                return
            if buf.complete_state:
                if self._select_completion(buf, max(1, event.arg)):
                    event.app.invalidate()
                return
            buf.start_completion(
                select_first=True,
                complete_event=CompleteEvent(completion_requested=True)
            )
            self._sync_completion_suggestion(buf)

        @kb.add("s-tab")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state:
                if self._select_completion(buf, -max(1, event.arg)):
                    event.app.invalidate()

        @kb.add("backspace", eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            if event.arg < 0:
                deleted = buf.delete(count=-event.arg)
            else:
                deleted = buf.delete_before_cursor(count=event.arg)
            if not deleted:
                event.app.output.bell()

        @kb.add(Keys.BracketedPaste, eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            data = (event.data or "").replace("\r\n", "\n").replace("\r", "\n")
            buf.cancel_completion()
            display_text = self._display_text_for_paste(data, current_text=buf.text)
            buf.insert_text(display_text)

        @kb.add("enter")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state and buf.complete_state.current_completion:
                completion = buf.complete_state.current_completion
                buf.apply_completion(completion)
                if completion.text == PromptToolkitBox.SKILLS_COMMAND_TEXT:
                    buf.start_completion(
                        select_first=False,
                        complete_event=CompleteEvent(text_inserted=True)
                    )
                    self._sync_completion_suggestion(buf)
                    event.app.invalidate()
                    return
                if completion.text.startswith("$"):
                    self._sync_completion_suggestion(buf)
                    event.app.invalidate()
                    return
                if completion.text in PromptToolkitBox.PARAMETERIZED_COMMANDS:
                    buf.suggestion = self.auto_suggest.get_suggestion(buf, buf.document)
                    buf.on_suggestion_set.fire()
                    event.app.invalidate()
                    return
            buf.validate_and_handle()

        @kb.add("up")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state:
                if self._select_completion(buf, -max(1, event.arg)):
                    event.app.invalidate()
                return
            buf.auto_up(count=event.arg, go_to_start_of_line_if_history_changes=True)

        @kb.add("down")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state:
                if self._select_completion(buf, max(1, event.arg)):
                    event.app.invalidate()
                return
            buf.auto_down(count=event.arg, go_to_start_of_line_if_history_changes=True)

        return kb

    def _get_session(self) -> PromptSession[str]:
        """延迟创建输入会话。"""
        if self.session is None:
            self.session = PromptSession(
                history=self.history,
                key_bindings=self.key_bindings
            )
        return self.session

    async def prompt_async(
        self,
        *,
        mode: RunMode,
        model: str,
        workspace_label: str = "",
        access_label: str = ""
    ) -> str:
        """异步输入渲染入口。"""
        th      = self._theme(mode)
        message = self._render_message(model, th, workspace_label, access_label)

        self.auto_suggest.set_mode(mode)

        session = self._get_session()

        with patch_stdout(raw=True):
            value = await session.prompt_async(
                message=message,
                lexer=self.lexer,
                completer=self.completer,
                auto_suggest=self.auto_suggest,
                complete_while_typing=True,
                complete_style=CompleteStyle.COLUMN,
                enable_history_search=True,
                multiline=True,
                prompt_continuation=self._render_continuation(),
                placeholder=HTML(
                    f"<placeholder> {html.escape(th['placeholder'])}</placeholder>"
                ),
                reserve_space_for_menu=4,
                style=self.style,
                mouse_support=False,
                pre_run=lambda: clear_pending_input(session.app.input)
            )

        try:
            return self._restore_pasted_content(value).strip()
        finally:
            self.paste_store.clear()


if __name__ == '__main__':
    pass
