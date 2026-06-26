# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import html
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
from mind_nova import const
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_core.terminal_input import clear_pending_input
from .commands import SlashCommandCompleter
from .ghost import (
    BASE_CODING_AGENT_TEMPLATES,
    CHAT_TEMPLATES,
    MODE_ALIAS_TEMPLATES,
    VERB_DOMAIN_WEIGHTS,
    build_intent_templates
)
from .skills import SkillTokenLexer


class PromptHeaderState:
    """输入头部的动态展示状态。"""

    def __init__(
        self,
        *,
        model: str = "",
        workspace_status: str = "?"
    ) -> None:
        self.model = str(model or "")
        self.workspace_status = str(workspace_status or "?")

    def update(
        self,
        *,
        model: typing.Optional[str] = None,
        workspace_status: typing.Optional[str] = None
    ) -> None:
        if model is not None:
            self.model = model
        if workspace_status is not None:
            self.workspace_status = workspace_status


class CommandAutoSuggest(AutoSuggest):
    """行内提示视图。"""

    SLASH_HINTS: dict[str, str] = {
        "/model": " <model-name>",
        "/model ": "<model-name>",
        "/apikey": " <api-key>",
        "/apikey ": "<api-key>",
        "/attach": " <path>",
        "/attach ": "<path>",
        "/detach": " <index-or-path>",
        "/detach ": "<index-or-path>"
    }

    MODE_ALLOWED_DOMAINS: dict[RunMode, frozenset[str]] = {
        "chat": frozenset({
            "coding_agent",
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
            "report"
        }),
        "fast": frozenset({
            "coding_agent",
            "inspect_runtime",
            "security",
            "network_http",
            "network_sse_ws",
            "network_graphql",
            "network_socket",
            "network_mail_file",
            "media_video",
            "media_audio",
            "report"
        }),
        "plan": frozenset({
            "coding_agent",
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
            "report"
        }),
        "xtra": frozenset({
            "coding_agent",
            "inspect_runtime",
            "security",
            "network_http",
            "network_sse_ws",
            "network_graphql",
            "network_socket",
            "network_mail_file",
            "report"
        }),
    }

    BASE_PREFERRED_PHRASES: dict[str, tuple[str, ...]] = {
        "查看": ("当前改动", "相关实现", "调用链路", "失败日志"),
        "分析": ("失败原因", "回归风险", "代码路径", "当前改动"),
        "检查": ("当前改动", "类型问题", "代码风格", "测试覆盖"),
        "审查": ("当前改动",),
        "评审": ("当前改动",),
        "定位": ("问题根因", "调用链路"),
        "排查": ("失败原因", "回归问题"),
        "复现": ("问题",),
        "修复": ("问题并跑测试", "测试失败", "类型错误", "展示问题"),
        "修改": ("代码并验证",),
        "改": ("代码并验证",),
        "实现": ("这个需求并验证",),
        "补": ("测试覆盖",),
        "补充": ("回归测试",),
        "运行": ("相关测试", "lint", "type check", "构建"),
        "跑": ("相关测试", "lint", "type check"),
        "验证": ("修改结果", "回归风险"),
        "构建": ("项目并修复失败",),
        "重构": ("相关实现并保持行为",),
        "搜索": ("相关代码",),
        "梳理": ("调用链路",),
        "总结": ("当前改动",),
        "review": (" current changes and identify risks",),
        "debug": (" reproduce, inspect, patch, and verify",),
        "inspect": (" related implementation and summarize findings",),
        "trace": (" the call path and locate the issue",),
        "fix": (" the issue and run tests",),
        "patch": (" the minimal change and verify",),
        "repro": (" the issue and locate the cause",),
        "run": (" relevant tests",),
        "verify": (" the change and summarize results",),
        "refactor": (" safely and verify behavior",),
        "search": (" related code",),
        "summarize": (" current changes",),
    }

    MODE_PREFERRED_PHRASES: dict[RunMode, dict[str, tuple[str, ...]]] = {
        "chat": {
            "生成": ("内存报告", "流畅度报告", "阶段帧分析报告"),
            "打开": ("设置", "应用", "录屏"),
            "执行": ("Monkey 测试",),
        },
        "fast": {
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
            "打开": ("设置", "通知栏", "应用"),
            "开": ("设置", "通知栏", "应用"),
            "进入": ("应用", "设置"),
            "返回": ("首页", "上一页"),
            "等待": ("元素出现", "元素消失", "3 秒"),
            "滚动": ("到目标元素", "到顶部", "到底部"),
        },
        "xtra": {
            "查询": ("数据库", "用户表", "订单表"),
            "执行": ("SQL", "查询语句"),
            "打开": ("网页", "控制台"),
        },
    }

    MODE_BLOCKED_FULL_PHRASES: dict[RunMode, frozenset[str]] = {
        "chat": frozenset(),
        "fast": frozenset(),
        "plan": frozenset({"循环执行步骤"}),
        "xtra": frozenset()
    }

    def __init__(self) -> None:
        self.mode: RunMode = DEFAULT_RUN_MODE
        self.chat_templates: tuple[tuple[str, str], ...] = CHAT_TEMPLATES
        self.intent_templates: tuple[dict[str, typing.Any], ...] = build_intent_templates()

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

            allowed_domains = self.MODE_ALLOWED_DOMAINS.get(self.mode, frozenset())
            blocked_phrases = self.MODE_BLOCKED_FULL_PHRASES.get(self.mode, frozenset())

            matched = [
                item
                for item in self.intent_templates
                if stripped == item["verb"]
                and item["domain"] in allowed_domains
                and f"{item['verb']}{item['suggestion']}" not in blocked_phrases
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
                if item["domain"] not in allowed_domains:
                    continue
                full = f"{item['verb']}{item['suggestion']}"
                if full in blocked_phrases:
                    continue
                if full.startswith(stripped) and full != stripped:
                    remain = full[len(stripped):]
                    prefix_intents.append((
                        len(remain),
                        self._phrase_priority(item["verb"], item["suggestion"]),
                        VERB_DOMAIN_WEIGHTS.get(item["verb"], {}).get(item["domain"], 0),
                        item["group_weight"],
                        -item["order"],
                        remain
                    ))

            if prefix_intents:
                prefix_intents.sort(
                    key=lambda item: (item[0], -item[1], -item[2], -item[3], item[4])
                )
                return Suggestion(prefix_intents[0][5])

        return None

    def _mode_alias_suggestion(self, text: str) -> typing.Optional[Suggestion]:
        """根据模式别名模板生成行内提示。"""
        stripped = text.strip().lower()
        if not stripped or stripped.startswith("/"):
            return None

        candidates: list[tuple[int, str]] = []

        blocked_phrases = self.MODE_BLOCKED_FULL_PHRASES.get(self.mode, frozenset())
        for prefix, suffix in (
            BASE_CODING_AGENT_TEMPLATES + MODE_ALIAS_TEMPLATES.get(self.mode, ())
        ):
            full = f"{prefix}{suffix}"
            if full in blocked_phrases:
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
        """返回指定动词和提示短语的优先级。"""
        preferred = (
            self.BASE_PREFERRED_PHRASES.get(verb, ())
            + self.MODE_PREFERRED_PHRASES.get(self.mode, {}).get(verb, ())
        )
        for idx, phrase in enumerate(preferred):
            if suggestion == phrase:
                return len(preferred) - idx
        return 0

    @staticmethod
    def _best_prefix_completion(
        text: str,
        pairs: tuple[tuple[str, str], ...],
    ) -> typing.Optional[Suggestion]:
        """从前缀模板中选择最短可用补全提示。"""
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


class PromptToolkitBox(object):
    """交互输入视图。"""

    PARAMETERIZED_COMMANDS: tuple[str, ...] = (
        "/model ", "/apikey ", "/attach ", "/detach "
    )
    SKILLS_COMMAND_TEXT: str = "$"

    MODEL_DISPLAY_MAX: int    = 24
    PASTE_CHAR_THRESHOLD: int = 1200
    PASTE_LINE_THRESHOLD: int = 20
    WORKSPACE_STATUS_DEFAULT: str = "?"

    def __init__(self) -> None:
        self.history: InMemoryHistory         = InMemoryHistory()
        self.completer: SlashCommandCompleter = SlashCommandCompleter()
        self.auto_suggest: CommandAutoSuggest = CommandAutoSuggest()

        self.lexer: SkillTokenLexer = SkillTokenLexer()

        self.key_bindings: KeyBindings = self._build_key_bindings()

        self.session: typing.Optional[PromptSession[str]] = None

        self.paste_store: dict[str, str] = {}

        self.style: Style = Style.from_dict({
            "prompt": "bold #E2E5EA",
            "prompt.kicker"                           : "bold #7B838E",
            "prompt.model"                            : "bold #F3F5F8",
            "prompt.muted"                            : "bold #767D87",
            "prompt.workspace.same"                   : "bold #7B838E",
            "prompt.workspace.diff"                   : "bold #D3C27C",
            "prompt.workspace.unknown"                : "bold #767D87",
            "placeholder"                             : "bold #727983",
            "auto-suggestion"                         : "#5A616A bg:#0A0D18",
            "skill-token"                             : "bold #8FD7FF",
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
    def _theme(mode: RunMode) -> dict[str, str]:
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
            },
            "xtra": {
                "brand": "#2DAA9E",
                "soft": "#1E7F78",
                "placeholder": "Xtra 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            }
        }[mode]

    @staticmethod
    def _clip_model_name(model: str, limit: int) -> str:
        """展示名称裁剪。"""
        if len(model) <= limit:
            return model
        return model[: max(0, limit - 3)] + "..."

    @staticmethod
    def _workspace_status_symbol(status: str) -> tuple[str, str]:
        """返回 workspace 状态符号和样式。"""
        normalized = str(status or "").strip()
        if normalized == "=":
            return "=", "prompt.workspace.same"
        if normalized == "!":
            return "!", "prompt.workspace.diff"
        return "?", "prompt.workspace.unknown"

    @staticmethod
    def _render_message(
        model: str,
        th: dict[str, str],
        workspace_status: str = WORKSPACE_STATUS_DEFAULT
    ) -> HTML:
        """输入头部渲染。"""
        safe_model = html.escape(
            PromptToolkitBox._clip_model_name(model or "-", PromptToolkitBox.MODEL_DISPLAY_MAX)
        )
        workspace_symbol, workspace_style = PromptToolkitBox._workspace_status_symbol(
            workspace_status
        )
        return HTML(
            f"<prompt>"
            f"<prompt.kicker>[</prompt.kicker> "
            f"<prompt.brand fg='{th['brand']}'>{html.escape(const.APP_DESC)}</prompt.brand> "
            f"<prompt.kicker>::</prompt.kicker> "
            f"<{workspace_style}>{html.escape(workspace_symbol)}</{workspace_style}> "
            f"<prompt.kicker>::</prompt.kicker> "
            f"<prompt.model fg='{th['soft']}'>{safe_model}</prompt.model> "
            f"<prompt.kicker>]</prompt.kicker>\n"
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
        workspace_status: str = WORKSPACE_STATUS_DEFAULT,
        header_state: typing.Optional[PromptHeaderState] = None
    ) -> str:
        """异步输入渲染入口。"""
        th = self._theme(mode)

        if header_state is not None:
            header_state.update(model=model, workspace_status=workspace_status)
            message = lambda: self._render_message(
                header_state.model,
                th,
                header_state.workspace_status
            )
        else:
            message = self._render_message(model, th, workspace_status)

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
                refresh_interval=0.5 if header_state is not None else None,
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
