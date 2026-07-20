# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import asyncio
import contextlib
from prompt_toolkit.application import (
    Application,
    in_terminal
)
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.base import Input
from prompt_toolkit.layout import (
    Dimension,
    Layout
)
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    VerticalAlign,
    VSplit,
    Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AfterInput,
    ConditionalProcessor
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import (
    Style,
    merge_styles
)
from prompt_toolkit.widgets import TextArea
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.render import APPROVAL_MENU_STYLE
from mind_app.interaction.contracts import PromptContext
from .models import MenuRequest
from .terminal_input import clear_pending_input
from mind_nova import const
from .activity import TuiActivity
from .approval import TuiApproval
from .document import TuiDocument
from .input import TuiInputModel
from .menu import TUI_MENU_STYLE, TuiMenu
from .render import (
    FormattedText,
    clip_fragments,
    cursor_point,
    display_line_count,
    fragments_text,
    renderable_fragments
)

_QUEUE_END = object()


class TuiRuntime(object):
    """管理稳定画布上的会话内容、输入队列和审批交互。"""

    INPUT_MAX_LINES: typing.Final[int]     = 8
    APPROVAL_MAX_WIDTH: typing.Final[int]  = 104
    APPROVAL_MAX_HEIGHT: typing.Final[int] = 14

    def __init__(
        self,
        input_model: TuiInputModel | None = None,
        *,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
    ) -> None:
        self.input_model = input_model or TuiInputModel()
        self.context = PromptContext(mode="chat", model="")
        self.placeholder_text = self.input_model.new_placeholder(self.context.mode)

        self.message_queue: asyncio.Queue[typing.Any] = asyncio.Queue()
        self.document = TuiDocument()
        self.status_renderable: typing.Any = None

        self._application_task: asyncio.Task[None] | None = None
        self._application_error: BaseException | None = None
        self._closing = False

        self.activity = TuiActivity(
            set_renderable=self.set_status_renderable,
            clear_renderable=self.clear_status_renderable,
        )

        self.input = TextArea(
            multiline=True,
            lexer=self.input_model.lexer,
            auto_suggest=self.input_model.auto_suggest,
            completer=self.input_model.completer,
            complete_while_typing=True,
            accept_handler=self._accept_input,
            history=self.input_model.history,
            wrap_lines=True,
            height=self._input_dimension,
            dont_extend_height=True,
            get_line_prefix=self._input_line_prefix,
            input_processors=[
                ConditionalProcessor(
                    AfterInput(self._placeholder_fragments),
                    filter=Condition(lambda: not self.input.buffer.text),
                )
            ],
        )
        self.input.buffer.enable_history_search = True

        self.transcript_control = FormattedTextControl(
            self._transcript_fragments,
            get_cursor_position=self._transcript_cursor,
        )
        self.status_control = FormattedTextControl(self._status_fragments)
        self.footer_control = FormattedTextControl(self._footer_fragments)

        self.approval = TuiApproval(
            invalidate=self.invalidate,
            focus_card=lambda: self.application.layout.focus(self.approval_control),
            focus_input=lambda: self.application.layout.focus(self.input),
        )
        self.approval_control = FormattedTextControl(
            self.approval.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.approval.key_bindings,
        )
        self.menu = TuiMenu(
            invalidate=self.invalidate,
            focus_menu=lambda: self.application.layout.focus(self.menu_control),
            focus_input=lambda: self.application.layout.focus(self.input),
        )
        self.menu_control = FormattedTextControl(
            self.menu.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )

        self.transcript_window = Window(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.status_window = Window(
            content=self.status_control,
            height=self._status_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.approval_window = Window(
            content=self.approval_control,
            width=self._approval_width,
            height=self._approval_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            style="class:approval-card",
        )
        self.menu_window = Window(
            content=self.menu_control,
            height=self._menu_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.footer_window = Window(
            content=self.footer_control,
            height=Dimension.exact(1),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )

        self.approval_card = ConditionalContainer(
            VSplit(
                [
                    Window(width=Dimension.exact(2), char=" "),
                    self.approval_window,
                    Window(char=" "),
                ],
                height=self._approval_dimension,
            ),
            filter=Condition(lambda: self.approval.active),
        )
        self.menu_card = ConditionalContainer(
            VSplit(
                [
                    Window(width=Dimension.exact(2), char=" "),
                    self.menu_window,
                ],
                height=self._menu_dimension,
            ),
            filter=Condition(lambda: self.menu.active),
        )
        self.content_input_gap = ConditionalContainer(
            Window(height=Dimension.exact(1), char=" "),
            filter=Condition(self._has_middle_content),
        )
        self.input_footer_gap = Window(
            height=Dimension.exact(1),
            char=" ",
            dont_extend_height=True,
        )
        self.input_stack = HSplit(
            [self.input, self.input_footer_gap, self.footer_window],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
        )
        self.canvas = HSplit(
            [
                self.transcript_window,
                self.approval_card,
                self.menu_card,
                self.status_window,
                self.content_input_gap,
                self.input_stack,
            ],
            align=VerticalAlign.TOP,
            height=self._canvas_dimension,
        )

        root = FloatContainer(
            content=self.canvas,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    transparent=True,
                    content=CompletionsMenu(
                        max_height=8,
                        scroll_offset=1,
                        display_arrows=False,
                    ),
                )
            ],
        )

        dummy_io = (
            input_obj is None
            and output_obj is None
            and not (sys.stdin.isatty() and sys.stdout.isatty())
        )
        application_input = input_obj or (DummyInput() if dummy_io else None)
        application_output = output_obj or (DummyOutput() if dummy_io else None)
        self.application: Application[None] = Application(
            layout=Layout(root, focused_element=self.input),
            key_bindings=self.input_model.key_bindings,
            style=self._style(),
            full_screen=False,
            erase_when_done=False,
            mouse_support=False,
            max_render_postpone_time=None,
            input=application_input,
            output=application_output,
        )

    @property
    def active(self) -> bool:
        """返回 TUI 应用是否正在运行。"""
        task = self._application_task
        return task is not None and not task.done()

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        size = self._output_size()
        return max(20, size[0])

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        size = self._output_size()
        return max(8, size[1])

    async def open(self) -> None:
        """启动持久非全屏输入应用并等待首帧完成。"""
        if self.active:
            return None

        self._closing = False
        self._application_error = None
        previous_render_count = self.application.render_counter
        self._application_task = asyncio.create_task(
            self._run_application(),
            name="mind tui",
        )
        while (
            (
                not self.application.is_running
                or self.application.render_counter == previous_render_count
            )
            and not self._application_task.done()
        ):
            await asyncio.sleep(0)

    async def close(self) -> None:
        """停止输入应用和全部动态任务。"""
        self._closing = True
        await self.end_activity_status()
        await self.approval.close()
        await self.menu.close()
        await self._exit_application(erase=False)

    async def run_modal(
        self,
        factory: typing.Callable[[], typing.Awaitable[typing.Any]],
    ) -> typing.Any:
        """在同一个 Application 任务中暂时让出终端。"""
        context = self.application.context
        if not self.active or context is None:
            return await factory()

        async def invoke() -> typing.Any:
            async with in_terminal(render_cli_done=False):
                return await factory()

        task = context.copy().run(lambda: asyncio.create_task(invoke()))
        return await task

    async def read_message(self, context: PromptContext) -> str:
        """更新输入上下文并按提交顺序读取下一条消息。"""
        self.context = context
        self.placeholder_text = self.input_model.new_placeholder(context.mode)
        self.input_model.set_mode(context.mode)
        self.invalidate()

        value = await self.message_queue.get()
        self.invalidate()
        if value is _QUEUE_END:
            if self._application_error is not None:
                raise self._application_error
            raise EOFError
        return str(value)

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """在唯一审批区域中读取工具执行决策。"""
        return await self.approval.request(approval)

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """在主 Application 画布内读取菜单选择。"""
        return await self.menu.request(request)

    def update_menu(self, request: MenuRequest) -> None:
        """更新主画布中的菜单或只读面板。"""
        self.menu.update(request)

    def finish_menu(self, value: typing.Any = None) -> None:
        """结束主画布中的菜单或只读面板。"""
        self.menu.finish(value)

    def append_block(self, renderable: typing.Any) -> None:
        """向会话内容追加一个稳定展示块。"""
        if self.document.append_block(renderable):
            self.invalidate()

    def append_gap(self) -> None:
        """请求在下一项正文前保留一个视觉空行。"""
        self.document.request_gap()

    def set_active_renderable(self, renderable: typing.Any) -> None:
        """替换当前流式展示块。"""
        self.document.set_active(renderable)
        self.invalidate()

    def commit_active_renderable(self, renderable: typing.Any) -> None:
        """把当前动态正文替换为同位置的稳定块。"""
        self.document.commit_active(renderable)
        self.invalidate()

    def clear_active_renderable(self) -> None:
        """清空当前流式展示块。"""
        self.document.clear_active()
        self.invalidate()

    def set_status_renderable(self, renderable: typing.Any) -> None:
        """替换动画专属区域内容。"""
        self.status_renderable = renderable
        self.invalidate()

    def clear_status_renderable(self) -> None:
        """清空动画专属区域内容。"""
        self.status_renderable = None
        self.invalidate()

    async def begin_mode_status(self, mode: str) -> None:
        """启动模式等待动画。"""
        await self.activity.begin_mode(mode)

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动附件上传动画。"""
        await self.activity.begin_upload(snapshot)

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动内置运行时状态动画。"""
        await self.activity.begin_inbuild(snapshot)

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self.activity.begin_external_mcp(snapshot)

    async def end_activity_status(self) -> None:
        """结束运行期活动动画。"""
        await self.activity.stop()

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        if self.active and not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.invalidate()

    async def _run_application(self) -> None:
        """运行输入应用并传播终端结束状态。"""
        try:
            with create_app_session(
                input=self.application.input,
                output=self.application.output,
            ):
                with patch_stdout(raw=True):
                    await self.application.run_async(
                        pre_run=lambda: clear_pending_input(self.application.input)
                    )
        except (EOFError, KeyboardInterrupt) as exc:
            self._application_error = exc
        except BaseException as exc:
            self._application_error = exc
        finally:
            if not self._closing:
                self.message_queue.put_nowait(_QUEUE_END)

    async def _exit_application(self, *, erase: bool) -> None:
        """结束当前应用任务并按需清除画布。"""
        task = self._application_task
        if task is None:
            return None

        self.application.erase_when_done = erase
        if not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.exit(result=None)
        with contextlib.suppress(asyncio.CancelledError, EOFError):
            await task
        self._application_task = None
        self.application.erase_when_done = False

    def _accept_input(self, buffer) -> bool:
        """恢复折叠粘贴内容并把输入追加到队列。"""
        value = self.input_model.restore_submission(buffer.text)
        self.message_queue.put_nowait(value)
        self.input_model.clear_submission_state()
        self.invalidate()
        return False

    def _input_line_prefix(
        self,
        line_number: int,
        wrap_count: int,
    ) -> StyleAndTextTuples:
        """生成输入首行和续行的无边框前缀。"""
        if line_number == 0 and wrap_count == 0:
            return [("class:prompt.kicker", "> ")]
        return [("class:prompt.kicker", ". ")]

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return [("class:placeholder", f" {self.placeholder_text}")]

    def _transcript_fragments(self) -> FormattedText:
        """生成会话内容区域的格式化片段。"""
        return self.document.fragments(width=self.terminal_width)

    def _status_fragments(self) -> FormattedText:
        """生成动画专属区域的格式化片段。"""
        if self.status_renderable is None:
            return []
        return renderable_fragments(
            self.status_renderable,
            width=self.terminal_width,
        )

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        theme = self.input_model.theme(self.context.mode)
        parts: FormattedText = [(f"fg:{theme['brand']} bold", const.APP_DESC)]
        values = [
            ("class:prompt.model", self.context.model or "-"),
            ("class:prompt.access", self.context.access_label),
            ("class:prompt.workspace", self.context.workspace_label),
        ]
        if self.context.exec_status_label:
            values.append(("class:prompt.exec.command", self.context.exec_status_label))
        queued = self.message_queue.qsize()
        if queued:
            values.append(("class:prompt.exec", f"queued {queued}"))

        for style, value in values:
            text = str(value or "").strip()
            if text:
                parts.extend([
                    ("class:prompt.kicker", " · "),
                    (style, text),
                ])
        return clip_fragments(parts, width=self.terminal_width)

    def _transcript_cursor(self) -> Point:
        """让会话内容视口跟随最新输出。"""
        text = fragments_text(self._transcript_fragments())
        x, y = cursor_point(text, width=self.terminal_width)
        return Point(x=x, y=y)

    def _canvas_dimension(self) -> Dimension:
        """返回随可见内容增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _transcript_dimension(self) -> Dimension:
        """按剩余画布空间限制会话内容高度。"""
        text = fragments_text(self._transcript_fragments())
        rows = display_line_count(text, width=self.terminal_width)
        available = max(
            0,
            self.terminal_height
            - self._input_height()
            - 2
            - self._status_height()
            - self._approval_height()
            - self._menu_height()
            - int(self._has_middle_content()),
        )
        return Dimension.exact(min(rows, available))

    def _status_dimension(self) -> Dimension:
        """返回动画区域的精确高度。"""
        return Dimension.exact(self._status_height())

    def _input_dimension(self) -> Dimension:
        """返回输入框当前显示高度。"""
        return Dimension.exact(self._input_height())

    def _input_stack_dimension(self) -> Dimension:
        """返回输入框、单行间距和 footer 的固定总高度。"""
        return Dimension.exact(self._input_height() + 2)

    def _approval_dimension(self) -> Dimension:
        """返回审批卡当前显示高度。"""
        return Dimension.exact(self._approval_height())

    def _menu_dimension(self) -> Dimension:
        """返回内嵌菜单当前显示高度。"""
        return Dimension.exact(self._menu_height())

    def _status_height(self) -> int:
        """计算动画区域占用行数。"""
        text = fragments_text(self._status_fragments())
        if not text:
            return 0
        return min(3, max(1, display_line_count(text, width=self.terminal_width)))

    def _input_height(self) -> int:
        """计算输入内容占用的显示行数。"""
        rows = display_line_count(
            self.input.buffer.text,
            width=max(1, self.terminal_width - 2),
        )
        return max(1, min(self.INPUT_MAX_LINES, rows))

    def _approval_height(self) -> int:
        """计算审批卡在当前画布中的显示高度。"""
        if not self.approval.active:
            return 0
        text = fragments_text(self.approval.fragments())
        rows = display_line_count(text, width=self._approval_content_width())
        available = max(
            1,
            self.terminal_height - self._input_height() - self._status_height() - 3,
        )
        return min(rows, self.APPROVAL_MAX_HEIGHT, available)

    def _menu_height(self) -> int:
        """计算内嵌菜单在当前画布中的显示高度。"""
        if not self.menu.active:
            return 0
        available = max(
            1,
            self.terminal_height - self._input_height() - self._status_height() - 3,
        )
        return min(self.menu.height(), available)

    def _approval_width(self) -> Dimension:
        """返回审批卡宽度约束。"""
        return Dimension.exact(self._approval_content_width())

    def _approval_content_width(self) -> int:
        """计算审批卡内容宽度。"""
        return min(self.APPROVAL_MAX_WIDTH, max(20, self.terminal_width - 4))

    def _visible_height(self) -> int:
        """计算当前画布实际可见内容的高度。"""
        height = (
            self._transcript_dimension().preferred
            + self._status_height()
            + self._approval_height()
            + self._menu_height()
            + int(self._has_middle_content())
            + self._input_height()
            + 2
        )
        return max(1, min(self.terminal_height, height))

    def _has_middle_content(self) -> bool:
        """判断输入框上方是否存在可见内容。"""
        return bool(
            self.document.has_content
            or self.status_renderable is not None
            or self.approval.active
            or self.menu.active
        )

    def _output_size(self) -> tuple[int, int]:
        """读取应用输出尺寸并提供标准终端回退值。"""
        application = getattr(self, "application", None)
        if application is not None:
            with contextlib.suppress(Exception):
                size = application.output.get_size()
                return int(size.columns), int(size.rows)
        fallback = shutil.get_terminal_size(fallback=(100, 24))
        return fallback.columns, fallback.lines

    def _style(self):
        """创建仅审批卡使用背景色的 TUI 样式。"""
        base = self.input_model.style
        overrides = Style.from_dict({
            "auto-suggestion": "bg:default #5A616A",
            "completion-menu": "bg:default #D8DCE2",
            "completion-menu.completion": "bg:default bold #D6DBE2",
            "completion-menu.completion.current": (
                "bg:default bold underline #F4F7FA"
            ),
            "completion-menu.meta.completion": "bg:default #7D858F",
            "completion-menu.meta.completion.current": (
                "bg:default underline #D9E0E7"
            ),
            "scrollbar.background": "bg:default",
            "scrollbar.button": "bg:default #666D76",
            "approval-card": "bg:#2B2D31 #D8DCE2",
        })
        return merge_styles([
            base,
            APPROVAL_MENU_STYLE,
            TUI_MENU_STYLE,
            overrides,
        ])


if __name__ == '__main__':
    pass
