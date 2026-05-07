# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import html
import typing
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import (
    AutoSuggest, Suggestion
)
from prompt_toolkit.completion import (
    Completer, CompleteEvent, Completion
)
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style
from mind_nova import const
from mind_core.prompting_ghost import (
    CHAT_TEMPLATES,
    COMMAND_TEMPLATES,
    MODE_ALIAS_TEMPLATES,
    VERB_DOMAIN_WEIGHTS,
    build_intent_templates
)

RUN_MODE = typing.Literal["chat", "fast", "plan"]


class SlashCommandCompleter(Completer):
    """Slash command completer with templates for parameterized commands."""

    COMMANDS: tuple[dict[str, str], ...] = (
        {"text": "/chat", "display": "/chat", "meta": "切换到 Chat 模式"},
        {"text": "/fast", "display": "/fast", "meta": "切换到 Fast 模式"},
        {"text": "/plan", "display": "/plan", "meta": "切换到 Plan 模式"},
        {"text": "/help", "display": "/help", "meta": "查看帮助"},
        {"text": "/h", "display": "/h", "meta": "查看帮助"},
        {"text": "/license", "display": "/license", "meta": "查看授权"},
        {"text": "/lic", "display": "/lic", "meta": "查看授权"},
        {"text": "/quit", "display": "/quit", "meta": "退出会话"},
        {"text": "/q", "display": "/q", "meta": "退出会话"},
        {"text": "/model ", "display": "/model", "meta": "输入模型名"},
        {"text": "/apikey ", "display": "/apikey", "meta": "输入 API Key"},
        {"text": "/attach ", "display": "/attach", "meta": "添加本轮待发送附件"},
        {"text": "/attachments", "display": "/attachments", "meta": "查看待发送附件"},
        {"text": "/detach ", "display": "/detach", "meta": "移除待发送附件"},
        {"text": "/attach-clear", "display": "/attach-clear", "meta": "清空待发送附件"},
    )
    TOP_LEVEL: tuple[str, ...] = (
        "/chat", "/fast", "/plan", "/help", "/license",
        "/quit", "/model", "/apikey", "/attach", "/attachments",
        "/detach", "/attach-clear"
    )
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        if not stripped.startswith("/"):
            return

        token = stripped.splitlines()[-1]
        if " " in token and not token.startswith(("/model", "/apikey", "/attach", "/detach")):
            return

        if token == "/":
            candidates = [
                item for item in self.COMMANDS if item["display"] in self.TOP_LEVEL
            ]
            visible_limit = len(self.TOP_LEVEL)
        else:
            candidates = [
                item for item in self.COMMANDS
                if item["display"].startswith(token) or item["text"].startswith(token)
            ]
            visible_limit = 7

        for item in candidates[:visible_limit]:
            yield Completion(
                item["text"],
                start_position=-len(token),
                display=item["display"],
                display_meta=item["meta"]
            )


class CommandAutoSuggest(AutoSuggest):
    """Dim inline suggestions for parameterized slash commands."""

    SLASH_HINTS: dict[str, str] = {
        "/model": " <model-name>",
        "/model ": "<model-name>",
        "/apikey": " <api-key>",
        "/apikey ": "<api-key>",
        "/attach": " <path>",
        "/attach ": "<path>",
        "/detach": " <index-or-path>",
        "/detach ": "<index-or-path>",
    }

    PARAMETER_HINT_LINES: frozenset[str] = frozenset({
        "/model ",
        "/apikey ",
        "/attach ",
        "/detach ",
    })

    MODE_ALLOWED_DOMAINS: dict[RUN_MODE, frozenset[str]] = {
        "chat": frozenset({
            "device_connection",
            "app_lifecycle",
            "ui_action",
            "keyevent",
            "system",
            "file",
            "screen_capture",
            "package_info",
            "inspect_runtime",
            "security",
            "network_http",
            "network_sse_ws",
            "network_graphql",
            "network_socket",
            "network_mail_file",
            "performance_memrix",
            "performance_framix",
            "stability_monkey",
            "media_video",
            "media_audio",
            "report",
        }),
        "fast": frozenset({
            "inspect_runtime",
            "security",
            "network_http",
            "network_sse_ws",
            "network_graphql",
            "network_socket",
            "network_mail_file",
            "media_video",
            "media_audio",
            "report",
        }),
        "plan": frozenset({
            "device_connection",
            "app_lifecycle",
            "ui_action",
            "keyevent",
            "system",
            "file",
            "screen_capture",
            "package_info",
            "inspect_runtime",
            "performance_memrix",
            "performance_framix",
            "stability_monkey",
            "media_video",
            "media_audio",
            "report",
        }),
    }
    MODE_PREFERRED_PHRASES: dict[RUN_MODE, dict[str, tuple[str, ...]]] = {
        "chat": {
            "查看": ("设备信息", "页面结构", "HTTP 响应", "内存趋势", "视频信息"),
            "分析": ("接口响应", "视频帧", "内存趋势", "页面切换速度"),
            "生成": ("内存报告", "阶段帧分析报告", "执行结果"),
            "提取": ("关键帧", "场景帧", "音轨"),
            "打开": ("设置", "应用", "录屏"),
        },
        "fast": {
            "查看": ("HTTP 响应", "接口响应", "响应", "SSE 事件流", "WebSocket 消息", "视频信息"),
            "分析": ("HTTP 响应", "接口响应", "GraphQL 响应", "视频帧"),
            "生成": ("结果摘要", "执行结果"),
            "提取": ("关键帧", "场景帧", "音轨"),
            "请求": ("HTTP 接口", "GraphQL 接口"),
            "连接": ("WebSocket",),
            "看": ("接口响应", "HTTP 响应", "响应", "WebSocket 消息", "GraphQL 响应"),
            "连": ("接 WebSocket",),
            "抽取": ("音轨", "截图"),
            "抽": ("关键帧", "场景帧", "音轨", "截图"),
        },
        "plan": {
            "查看": ("设备信息", "当前控件树", "当前焦点"),
            "打开": ("设置", "通知栏", "应用"),
            "开": ("设置", "通知栏", "应用"),
            "进入": ("应用", "设置"),
            "返回": ("首页", "上一页"),
            "等待": ("元素出现", "元素消失", "3 秒"),
            "滚动": ("到目标元素", "到顶部", "到底部"),
        },
    }
    MODE_BLOCKED_FULL_PHRASES: dict[RUN_MODE, frozenset[str]] = {
        "chat": frozenset(),
        "fast": frozenset(),
        "plan": frozenset({"循环执行步骤"}),
    }

    def __init__(self) -> None:
        self.mode: RUN_MODE = "chat"
        self.templates: dict[str, str] = COMMAND_TEMPLATES
        self.chat_templates: tuple[tuple[str, str], ...] = CHAT_TEMPLATES
        self.intent_templates: tuple[dict[str, typing.Any], ...] = build_intent_templates()

    def set_mode(self, mode: RUN_MODE) -> None:
        self.mode = mode

    def _mode_alias_suggestion(self, text: str) -> typing.Optional[Suggestion]:
        stripped = text.strip().lower()
        if not stripped or stripped.startswith("/"):
            return None

        candidates: list[tuple[int, str]] = []
        for prefix, suffix in MODE_ALIAS_TEMPLATES[self.mode]:
            full = f"{prefix}{suffix}"
            if full in self.MODE_BLOCKED_FULL_PHRASES[self.mode]:
                continue
            if full == stripped:
                continue
            if full.startswith(stripped):
                remain = full[len(stripped):]
                if remain:
                    candidates.append((len(remain), remain))
            elif prefix.startswith(stripped):
                remain = prefix[len(stripped):] + suffix
                if remain:
                    candidates.append((len(remain), remain))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        return Suggestion(candidates[0][1])

    def _phrase_priority(self, verb: str, suggestion: str) -> int:
        preferred = self.MODE_PREFERRED_PHRASES[self.mode].get(verb, ())
        for idx, phrase in enumerate(preferred):
            if suggestion == phrase:
                return len(preferred) - idx
        return 0

    @staticmethod
    def _best_prefix_completion(
        text: str,
        pairs: tuple[tuple[str, str], ...],
    ) -> typing.Optional[Suggestion]:
        stripped = text.strip()
        if not stripped:
            return None

        candidates: list[tuple[int, str]] = []
        for prefix, suffix in pairs:
            full = f"{prefix}{suffix}"
            if full == stripped:
                continue
            if full.startswith(stripped):
                remain = full[len(stripped):]
                if remain:
                    candidates.append((len(remain), remain))
            elif prefix.startswith(stripped):
                remain = prefix[len(stripped):] + suffix
                if remain:
                    candidates.append((len(remain), remain))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        return Suggestion(candidates[0][1])

    def get_suggestion(self, buffer, document):
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

        for prefix, suggestion in self.chat_templates:
            if current_line == prefix:
                return Suggestion(suggestion)

        if not current_line.startswith("/"):
            alias_suggestion = self._mode_alias_suggestion(current_line)
            if alias_suggestion is not None:
                return alias_suggestion

            prefix_suggestion = self._best_prefix_completion(current_line, self.chat_templates)
            if prefix_suggestion is not None:
                return prefix_suggestion

        if not current_line.startswith("/"):
            stripped = current_line.strip()
            matched = [
                item
                for item in self.intent_templates
                if stripped == item["verb"]
                and item["domain"] in self.MODE_ALLOWED_DOMAINS[self.mode]
                and f"{item['verb']}{item['suggestion']}" not in self.MODE_BLOCKED_FULL_PHRASES[self.mode]
            ]
            if matched:
                matched.sort(
                    key=lambda item: (
                        self._phrase_priority(item["verb"], item["suggestion"]),
                        VERB_DOMAIN_WEIGHTS.get(item["verb"], {}).get(item["domain"], 0),
                        item["group_weight"],
                        -item["order"],
                    ),
                    reverse=True
                )
                return Suggestion(matched[0]["suggestion"])

            prefix_intents = []
            for item in self.intent_templates:
                if item["domain"] not in self.MODE_ALLOWED_DOMAINS[self.mode]:
                    continue
                full = f"{item['verb']}{item['suggestion']}"
                if full in self.MODE_BLOCKED_FULL_PHRASES[self.mode]:
                    continue
                if full.startswith(stripped) and full != stripped:
                    remain = full[len(stripped):]
                    prefix_intents.append((
                        len(remain),
                        self._phrase_priority(item["verb"], item["suggestion"]),
                        VERB_DOMAIN_WEIGHTS.get(item["verb"], {}).get(item["domain"], 0),
                        item["group_weight"],
                        -item["order"],
                        remain,
                    ))

            if prefix_intents:
                prefix_intents.sort(key=lambda item: (item[0], -item[1], -item[2], -item[3], item[4]))
                return Suggestion(prefix_intents[0][5])
        return None


class PromptToolkitBox(object):
    """Async prompt_toolkit wrapper for the CLI loop."""

    PARAMETERIZED_COMMANDS: tuple[str, ...] = ("/model ", "/apikey ", "/attach ", "/detach ")
    MODEL_DISPLAY_MAX: int = 24

    def __init__(self) -> None:
        self.history: InMemoryHistory = InMemoryHistory()
        self.key_bindings: KeyBindings = self._build_key_bindings()
        self.session: typing.Optional[PromptSession[str]] = None
        self.completer: SlashCommandCompleter = SlashCommandCompleter()
        self.auto_suggest: CommandAutoSuggest = CommandAutoSuggest()
        self.style: Style = Style.from_dict({
            "prompt": "bold #E2E5EA",
            "prompt.kicker": "bold #7B838E",
            "prompt.model": "bold #F3F5F8",
            "prompt.muted": "bold #767D87",
            "placeholder": "bold #727983",
            "auto-suggestion": "#5A616A bg:#0A0D18",
            "completion-menu": "bg:#111315 #D8DCE2",
            "completion-menu.completion": "bg:#111315 bold #D6DBE2",
            "completion-menu.completion.current": "bg:#3B4148 bold #F4F7FA",
            "completion-menu.meta.completion": "bg:#111315 #7D858F",
            "completion-menu.meta.completion.current": "bg:#3B4148 #D9E0E7",
            "scrollbar.background": "bg:#111315",
            "scrollbar.button": "bg:#666D76",
        })

    def _sync_completion_suggestion(self, buf) -> None:
        """Preview suggestion for the currently highlighted completion item."""
        completion = buf.complete_state.current_completion if buf.complete_state else None
        if completion and completion.text in self.PARAMETERIZED_COMMANDS:
            text = completion.text.rstrip()
            buf.suggestion = self.auto_suggest.get_suggestion(
                buf, buf.document.__class__(text=text, cursor_position=len(text))
            )
            buf.on_suggestion_set.fire()
            return

        if buf.suggestion is not None:
            buf.suggestion = None
            buf.on_suggestion_set.fire()

    def _build_key_bindings(self) -> KeyBindings:
        """Key bindings for copy and history navigation."""
        kb = KeyBindings()

        @kb.add("escape", "enter")
        @kb.add("c-o")
        def _(event) -> None:
            event.app.current_buffer.insert_text("\n")

        @kb.add("/")
        def _(event) -> None:
            buf = event.app.current_buffer
            buf.insert_text("/")
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
                buf.complete_next(count=event.arg)
                self._sync_completion_suggestion(buf)
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
                buf.complete_previous(count=event.arg)
                self._sync_completion_suggestion(buf)

        @kb.add(Keys.BracketedPaste, eager=True)
        def _(event) -> None:
            buf = event.app.current_buffer
            data = (event.data or "").replace("\r\n", "\n").replace("\r", "\n")
            buf.cancel_completion()
            buf.insert_text(data)

        @kb.add("enter")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state and buf.complete_state.current_completion:
                completion = buf.complete_state.current_completion
                buf.apply_completion(completion)
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
                buf.complete_previous(count=event.arg)
                self._sync_completion_suggestion(buf)
                return
            buf.auto_up(count=event.arg, go_to_start_of_line_if_history_changes=True)

        @kb.add("down")
        def _(event) -> None:
            buf = event.app.current_buffer
            if buf.complete_state:
                buf.complete_next(count=event.arg)
                self._sync_completion_suggestion(buf)
                return
            buf.auto_down(count=event.arg, go_to_start_of_line_if_history_changes=True)

        return kb

    def _get_session(self) -> PromptSession[str]:
        """Create prompt session lazily in a real terminal context."""
        if self.session is None:
            self.session = PromptSession(
                history=self.history,
                key_bindings=self.key_bindings
            )
        return self.session

    @staticmethod
    def _theme(mode: RUN_MODE) -> dict[str, str]:
        return {
            "chat": {
                "brand": "#4F8FC8",
                "soft": "#2F6FAD",
                "placeholder": "Chat 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            },
            "fast": {
                "brand": "#4FA37D",
                "soft": "#2E7D5B",
                "placeholder": "Fast 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            },
            "plan": {
                "brand": "#866FD1",
                "soft": "#6B57B8",
                "placeholder": "Plan 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            }
        }[mode]

    @staticmethod
    def _clip_model_name(model: str, limit: int) -> str:
        """Clip model name for display only."""
        if len(model) <= limit:
            return model
        return model[: max(0, limit - 3)] + "..."

    @staticmethod
    def _render_message(model: str, th: dict[str, str]) -> HTML:
        """Render the prompt header."""
        safe_model = html.escape(
            PromptToolkitBox._clip_model_name(model or "-", PromptToolkitBox.MODEL_DISPLAY_MAX)
        )
        return HTML(
            f"<prompt>"
            f"<prompt.kicker>[</prompt.kicker> "
            f"<prompt.brand fg='{th['brand']}'>{html.escape(const.APP_DESC)}</prompt.brand> "
            f"<prompt.kicker>::</prompt.kicker> "
            f"<prompt.model fg='{th['soft']}'>{safe_model}</prompt.model> "
            f"<prompt.kicker>]</prompt.kicker>\n"
            f"<prompt.kicker>></prompt.kicker> "
            f"</prompt>"
        )

    @staticmethod
    def _render_continuation() -> HTML:
        """Render the prompt continuation prefix."""
        return HTML(
            f"<prompt.kicker>.</prompt.kicker> "
        )

    async def prompt_async(self, *, mode: RUN_MODE, model: str) -> str:
        """Render a themed async prompt."""
        th = self._theme(mode)
        message = self._render_message(model, th)
        self.auto_suggest.set_mode(mode)

        with patch_stdout(raw=True):
            value = await self._get_session().prompt_async(
                message=message,
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
                mouse_support=False
            )
        return value.strip()


if __name__ == '__main__':
    pass
