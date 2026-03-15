#  ____                            _   _
# |  _ \ _ __ ___  _ __ ___  _ __ | |_(_)_ __   __ _
# | |_) | '__/ _ \| '_ ` _ \| '_ \| __| | '_ \ / _` |
# |  __/| | | (_) | | | | | | |_) | |_| | | | | (_| |
# |_|   |_|  \___/|_| |_| |_| .__/ \__|_|_| |_|\__, |
#                           |_|                |___/
#

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
from mindnova import const

PROMPT_TAG = typing.Literal["CHAT", "FAST", "PLAN"]


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
        {"text": "/subscription", "display": "/subscription", "meta": "查看订阅"},
        {"text": "/sub", "display": "/sub", "meta": "查看订阅"},
        {"text": "/quit", "display": "/quit", "meta": "退出会话"},
        {"text": "/q", "display": "/q", "meta": "退出会话"},
        {"text": "/model ", "display": "/model", "meta": "输入模型名"},
        {"text": "/apikey ", "display": "/apikey", "meta": "输入 API Key"},
    )
    TOP_LEVEL: tuple[str, ...] = (
        "/chat", "/fast", "/plan", "/help", "/quit", "/model", "/apikey"
    )
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        if not stripped.startswith("/"):
            return

        token = stripped.splitlines()[-1]
        if " " in token and not token.startswith(("/model", "/apikey")):
            return

        if token == "/":
            candidates = [
                item for item in self.COMMANDS if item["display"] in self.TOP_LEVEL
            ]
        else:
            candidates = [
                item for item in self.COMMANDS
                if item["display"].startswith(token) or item["text"].startswith(token)
            ]

        for item in candidates[:7]:
            yield Completion(
                item["text"],
                start_position=-len(token),
                display=item["display"],
                display_meta=item["meta"]
            )


class CommandAutoSuggest(AutoSuggest):
    """Dim inline suggestions for parameterized slash commands."""

    INTENT_GROUPS: tuple[tuple[str, int, tuple[tuple[str, str], ...]], ...] = (
        ("screen", 80, (
            ("打开", "投屏"),
            ("进入", "投屏"),
            ("启动", "投屏"),
            ("关闭", "投屏"),
            ("停止", "投屏"),
            ("开始", "投屏"),
            ("打开", "录屏"),
            ("开始", "录屏"),
            ("启动", "录屏"),
            ("停止", "录屏"),
            ("结束", "录屏"),
            ("保存", "录屏"),
            ("录", "屏"),
            ("录屏", ""),
            ("截", "图"),
            ("截图", ""),
            ("截取", "截图"),
            ("保存", "截图"),
            ("打开", "截图"),
            ("拉取", "截图"),
        )),
        ("device", 70, (
            ("连接", "设备"),
            ("连上", "设备"),
            ("连接到", "设备"),
            ("选择", "设备"),
            ("切换到", "设备"),
            ("查看", "设备信息"),
            ("获取", "设备信息"),
        )),
        ("app", 60, (
            ("打开", "应用"),
            ("进入", "应用"),
            ("启动", "应用"),
            ("关闭", "应用"),
            ("停止", "应用"),
            ("安装", "应用"),
            ("卸载", "应用"),
            ("重启", "应用"),
        )),
        ("inspect", 75, (
            ("查看", "日志"),
            ("检查", "日志"),
            ("获取", "日志"),
            ("导出", "日志"),
            ("查看", "页面结构"),
            ("获取", "页面结构"),
            ("查看", "控件树"),
            ("分析", "页面结构"),
        )),
        ("network", 72, (
            ("爬", "接口"),
            ("抓", "接口"),
            ("拉", "接口"),
            ("获取", "接口"),
            ("查看", "接口"),
            ("分析", "接口"),
            ("爬", "请求"),
            ("抓", "请求"),
            ("拉", "请求"),
            ("获取", "响应"),
            ("查看", "响应"),
            ("分析", "响应"),
        )),
        ("performance", 85, [
            ("查看", "内存"),
            ("分析", "内存"),
            ("测试", "内存"),
            ("查看", "流畅度"),
            ("分析", "流畅度"),
            ("测试", "流畅度"),
            ("查看", "启动速度"),
            ("分析", "启动速度"),
            ("测试", "启动速度"),
            ("查看", "页面切换速度"),
            ("分析", "页面切换速度"),
            ("测试", "页面切换速度"),
            ("查看", "流式tokens"),
            ("分析", "流式tokens"),
            ("测试", "流式tokens"),
            ("查看", "首字上屏"),
            ("分析", "首字上屏"),
            ("测试", "首字上屏"),
            ("查看", "尾字上屏"),
            ("分析", "尾字上屏"),
            ("测试", "尾字上屏"),
            ("压测", "内存"),
            ("压测", "流畅度"),
            ("压测", "启动速度"),
            ("压测", "页面切换速度"),
        ]),
        ("report", 90, (
            ("生成", "内存报告"),
            ("输出", "内存报告"),
            ("导出", "内存报告"),
            ("生成", "阶段帧分析报告"),
            ("输出", "阶段帧分析报告"),
            ("导出", "阶段帧分析报告"),
            ("生成", "流畅度分析报告"),
            ("输出", "流畅度分析报告"),
            ("导出", "流畅度分析报告"),
        )),
        ("system", 50, (
            ("打开", "设置"),
            ("进入", "设置"),
            ("返回", "首页"),
            ("回到", "首页"),
            ("清理", "缓存"),
        )),
    )
    VERB_DOMAIN_WEIGHTS: dict[str, dict[str, int]] = {
        "查看": {"performance": 100, "inspect": 95, "device": 90, "network": 85},
        "获取": {"network": 100, "device": 95, "inspect": 90},
        "分析": {"performance": 100, "inspect": 95, "network": 90},
        "测试": {"performance": 100},
        "压测": {"performance": 100},
        "生成": {"report": 100},
        "输出": {"report": 100},
        "导出": {"report": 100, "inspect": 90},
        "打开": {"screen": 100, "app": 90, "system": 80},
        "进入": {"screen": 100, "app": 90, "system": 80},
        "启动": {"screen": 100, "app": 90},
        "关闭": {"screen": 100, "app": 90},
        "停止": {"screen": 100, "app": 90},
        "开始": {"screen": 100, "performance": 85},
        "连接": {"device": 100},
        "连上": {"device": 100},
        "连接到": {"device": 100},
        "选择": {"device": 100},
        "切换到": {"device": 100},
        "安装": {"app": 100},
        "卸载": {"app": 100},
        "重启": {"app": 100},
        "爬": {"network": 100},
        "抓": {"network": 100},
        "拉": {"network": 100},
        "拉取": {"screen": 95, "network": 90},
        "截": {"screen": 100},
        "截图": {"screen": 100},
        "截取": {"screen": 100},
        "录": {"screen": 100},
        "录屏": {"screen": 100},
        "返回": {"system": 100},
        "回到": {"system": 100},
        "清理": {"system": 100},
    }

    def __init__(self) -> None:
        self.templates: dict[str, str] = {
            "/model": " your-model-name",
            "/model ": "your-model-name",
            "/ap": "ikey ",
            "/api": "key ",
            "/apikey": " your-api-key",
            "/apikey ": "your-api-key",
            "/su": "bscription",
            "/sub": "scription",
            "/li": "cense",
            "/lic": "ense",
            "ex": "it",
            "qui": "t",
            "su": "bscription",
            "sub": "scription",
            "li": "cense",
            "lic": "ense",
        }
        self.chat_templates: tuple[tuple[str, str], ...] = (
            ("你", "好"),
            ("你好", "，请介绍一下你自己"),
            ("请", "介绍一下你自己"),
            ("介绍", "一下你自己"),
            ("你是", "谁"),
            ("你能", "做什么"),
            ("帮我", "分析一下这个问题"),
            ("帮我看", "一下这个问题"),
            ("解释", "一下这个问题"),
            ("说说", "这个功能"),
            ("总结", "一下这个内容"),
            ("给我", "一个方案"),
            ("怎么", "做"),
            ("怎么", "实现"),
            ("如何", "做"),
            ("如何", "实现"),
            ("怎样", "做"),
            ("怎样", "实现"),
            ("哪样", "更合适"),
            ("为什么", "会这样"),
            ("是否", "可以这样做"),
            ("能不能", "这样做"),
            ("可不可以", "这样做"),
            ("有没有", "更好的方案"),
        )
        self.intent_templates: tuple[dict[str, typing.Any], ...] = tuple(
            {
                "domain": domain,
                "group_weight": weight,
                "verb": verb,
                "suggestion": suggestion,
                "order": order,
            }
            for domain, weight, pairs in self.INTENT_GROUPS
            for order, (verb, suggestion) in enumerate(pairs)
        )

    def get_suggestion(self, buffer, document):
        text = document.text_before_cursor

        for prefix, suggestion in self.templates.items():
            if text == prefix:
                return Suggestion(suggestion)

        for prefix, suggestion in self.chat_templates:
            if text == prefix:
                return Suggestion(suggestion)

        if not text.startswith("/"):
            stripped = text.strip()
            matched = [
                item for item in self.intent_templates if stripped == item["verb"]
            ]
            if matched:
                matched.sort(
                    key=lambda item: (
                        self.VERB_DOMAIN_WEIGHTS.get(item["verb"], {}).get(item["domain"], 0),
                        item["group_weight"],
                        -item["order"],
                    ),
                    reverse=True
                )
                return Suggestion(matched[0]["suggestion"])
        return None


class PromptToolkitBox(object):
    """Async prompt_toolkit wrapper for the CLI loop."""

    PARAMETERIZED_COMMANDS: tuple[str, ...] = ("/model ", "/apikey ")
    MODEL_DISPLAY_MAX: int = 24

    def __init__(self) -> None:
        self.history: InMemoryHistory = InMemoryHistory()
        self.key_bindings: KeyBindings = self._build_key_bindings()
        self.session: typing.Optional[PromptSession[str]] = None
        self.completer: SlashCommandCompleter = SlashCommandCompleter()
        self.auto_suggest: CommandAutoSuggest = CommandAutoSuggest()
        self.style: Style = Style.from_dict({
            "prompt": "bold #E2E5EA",
            "prompt.kicker": "bold #949BA6",
            "prompt.model": "bold #F3F5F8",
            "prompt.muted": "bold #848B96",
            "placeholder": "bold #727983",
            "auto-suggestion": "bold #7C828C bg:#0A0D18",
            "completion-menu": "bg:#111315 #D8DCE2",
            "completion-menu.completion": "bg:#111315 bold #D6DBE2",
            "completion-menu.completion.current": "bg:#464B52 bold #FFFFFF",
            "completion-menu.meta.completion": "bg:#111315 bold #8C939C",
            "completion-menu.meta.completion.current": "bg:#464B52 bold #E7EBF0",
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
    def _theme(tag: PROMPT_TAG) -> dict[str, str]:
        return {
            "CHAT": {
                "brand": "#74B6FF",
                "soft": "#E8EEF3",
                "placeholder": "Chat 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            },
            "FAST": {
                "brand": "#72D7A6",
                "soft": "#E9EFEA",
                "placeholder": "Fast 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            },
            "PLAN": {
                "brand": "#A99BFF",
                "soft": "#EEEBF5",
                "placeholder": "Plan 输入 / 查看命令；Enter 发送，Alt+Enter 换行，↑/↓"
            }
        }[tag]

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

    async def prompt_async(self, *, tag: PROMPT_TAG, model: str) -> str:
        """Render a themed async prompt."""
        th = self._theme(tag)
        message = self._render_message(model, th)

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
                    f"<placeholder>{html.escape(th['placeholder'])}</placeholder>"
                ),
                reserve_space_for_menu=4,
                style=self.style,
                mouse_support=False
            )
        return value.strip()


if __name__ == '__main__':
    pass
