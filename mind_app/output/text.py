# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
from mind_nova import const
from mind_app.presentation.terminal_text import (
    sanitize_terminal_line,
    sanitize_terminal_text
)
from mind_app.presentation.contracts import (
    PresentationSink,
    PresentationView
)
from mind_app.presentation.models import (
    ApprovalView,
    BatchCompletedView,
    BatchStartView,
    FailureView,
    GenericToolResultView,
    HookRunView,
    LifecycleView,
    NativeToolResultView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
    ToolStartView
)
from ..stream_io.output_record import StreamRecordWriter
from .content import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ContentOutput,
    ContentSink,
    SourcesOutput
)
from .contracts import (
    OutputControlPort,
    OutputStatusPort
)
from .session import OutputSession
from mind_app.presentation.renderers.hook import render_hook_run_view
from mind_app.presentation.renderers.approval import render_approval_view

ANSI_RESET   = "\x1b[0m"
ANSI_BOLD    = "\x1b[1m"
ANSI_CYAN    = "\x1b[1;96m"
ANSI_MAGENTA = "\x1b[1;95m"


class TextStream(typing.Protocol):
    """定义文本输出只依赖的最小流能力。"""

    def write(self, text: str) -> int:
        """写入文本并返回已接收字符数。"""
        ...

    def flush(self) -> None:
        """刷新已写入内容。"""
        ...

    def isatty(self) -> bool:
        """返回当前流是否连接交互终端。"""
        ...


def _write(stream: TextStream, text: str) -> None:
    """写入并刷新一个文本块。"""
    if not text:
        return None
    stream.write(text)
    stream.flush()


def _terminal_text(value: typing.Any) -> str:
    """移除外部文本中的终端控制序列。"""
    return sanitize_terminal_text(value)


def _styled_text(text: str, style: str) -> str:
    """生成在末尾换行前复位的 ANSI 文本。"""
    body     = text.rstrip("\r\n")
    trailing = text[len(body):]

    if not body:
        return trailing

    return f"{style}{body}{ANSI_RESET}{trailing}"


def _supports_color(stream: TextStream) -> bool:
    """判断输出流是否适合写入 ANSI 样式。"""
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR") not in {None, "", "0"}:
        return True

    return stream.isatty()


def _line(value: typing.Any) -> str:
    """把值转换为单行文本。"""
    return sanitize_terminal_line(value)


def _tool_name(name: str) -> str:
    """返回适合终端展示的工具名称。"""
    return _line(name) or "tool"


def _payload(data: typing.Any) -> dict[str, typing.Any]:
    """提取工具结果中的字典载荷。"""
    return data if isinstance(data, dict) else {}


def _tool_output(data: typing.Any) -> str:
    """提取工具结果中的标准输出文本。"""
    payload  = _payload(data)
    combined = payload.get("output")

    if combined is not None and str(combined):
        return str(combined).rstrip("\n")

    values = [
        str(payload[key]).rstrip("\n")
        for key in ("stdout", "stderr")
        if payload.get(key) is not None and str(payload[key])
    ]

    return "\n".join(values)


class TextOutputState:
    """保存文本输出所需的流和记录状态。"""

    def __init__(
        self,
        record_writer: StreamRecordWriter,
        stdout: TextStream,
        stderr: TextStream,
        color: bool = False
    ) -> None:
        """绑定文本流、记录器和颜色配置。"""
        self.record_writer  = record_writer
        self.stdout         = stdout
        self.stderr         = stderr
        self.color          = color
        self.assistant_open = False

        self._assistant_parts: list[str] = []

    async def open(self) -> None:
        """打开文本记录。"""
        await self.record_writer.open()

    async def close(self) -> None:
        """关闭文本记录。"""
        if self.color:
            _write(self.stderr, ANSI_RESET)
        await self.record_writer.close()

    def process(self, text: str, *, style: str = "") -> None:
        """输出面向操作者的过程文本。"""
        plain = _terminal_text(text)
        visible = (
            _styled_text(plain, style)
            if style and self.color
            else plain
        )
        _write(self.stderr, visible)
        self.record_writer.write(plain, block=True)

    def metadata(self, label: str, value: typing.Any) -> None:
        """输出一行带高亮字段名的启动元数据。"""
        self.process(f"{label}:", style=ANSI_BOLD)
        self.process(f" {value}\n")

    def assistant(self, text: str) -> None:
        """输出 assistant 正文。"""
        plain = _terminal_text(text)

        if not plain:
            return None
        if not self.assistant_open:
            self.process(f"{const.APP_DESC.lower()}\n", style=ANSI_MAGENTA)
            self.assistant_open = True

        _write(self.stdout, plain)
        self.record_writer.write(plain)

    def append_assistant(self, text: str) -> None:
        """追加一段 assistant 正文。"""
        if text:
            self._assistant_parts.append(str(text))

    def flush_assistant(self) -> None:
        """一次输出当前 assistant 增量。"""
        if not self._assistant_parts:
            return None
        text = "".join(self._assistant_parts)
        self._assistant_parts.clear()
        self.assistant(text)

    def settle_assistant(self) -> None:
        """结束一段 assistant 正文。"""
        self.flush_assistant()
        if not self.assistant_open:
            return None
        if self.record_writer.trailing_newlines < 1:
            _write(self.stdout, "\n")
            self.record_writer.write("\n")
        self.assistant_open = False


class TextOutputControl(OutputControlPort, OutputStatusPort):
    """提供无动画的文本输出控制。"""

    def __init__(self, state: TextOutputState) -> None:
        """绑定共享的文本输出状态。"""
        self.state = state

    async def open(self) -> None:
        """打开文本输出。"""
        await self.state.open()

    async def stop(self, *, blink: bool = True) -> None:
        """停止文本输出。"""
        _ = blink
        self.state.settle_assistant()
        await self.state.close()

    async def begin_tool_status(self) -> None:
        """文本模式不显示动态工具状态。"""
        return None

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """文本模式输出一次自定义状态标题。"""
        if text:
            self.state.process(f"{text}\n")

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None
    ) -> None:
        """文本模式不显示等待动画。"""
        _ = text, delay_sec, animate_after_sec
        return None

    async def end_status(self, *, immediate: bool = False) -> None:
        """结束文本状态。"""
        _ = immediate
        return None

    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的文本。"""
        if text:
            self.state.record_writer.write(_terminal_text(text), block=True)

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None
    ) -> None:
        """输出工具调用标题和参数摘要。"""
        self.state.settle_assistant()

        tool = _tool_name(name)
        args = arguments if isinstance(arguments, dict) else {}

        if tool in {"shell_command", "exec_command", "write_stdin"}:
            command = _line(args.get("command") or args.get("cmd") or tool)
            cwd = _line(args.get("cwd") or ".")
            self.state.process("exec", style=ANSI_CYAN)
            self.state.process(f"\n{command} in {cwd}\n")
        elif tool == "js_repl":
            code = sanitize_terminal_text(args.get("code") or "").strip("\n")
            self.state.process("JavaScript", style=ANSI_CYAN)
            self.state.process(f"\n{code}\n" if code else "\n")
        else:
            self.state.process(tool, style=ANSI_CYAN)
            self.state.process("\n")

        safe_call_id = sanitize_terminal_line(call_id)

        if safe_call_id:
            self.state.record_writer.write_audit(
                f"tool {tool} call_id={safe_call_id}"
            )

    def flush(self) -> None:
        """刷新文本记录。"""
        self.state.record_writer.flush()


class TextContentSink(ContentSink):
    """把结构化正文写入文本输出流。"""

    def __init__(self, state: TextOutputState) -> None:
        """绑定正文使用的文本输出状态。"""
        self.state = state

    async def emit(self, output: ContentOutput) -> None:
        """输出正文或忽略来源元数据。"""
        if isinstance(output, AssistantTextDelta):
            self.state.append_assistant(output.text)
            return None
        if isinstance(output, (AssistantSegmentCompleted, AssistantOutputBoundary)):
            self.state.settle_assistant()
            return None
        if isinstance(output, AssistantPresentationSuperseded):
            self.state.settle_assistant()
            self.state.process("↻ Previous attempt interrupted; retrying\n")
            return None
        if isinstance(output, SourcesOutput):
            return None

        raise TypeError(f"Unsupported content output: {type(output).__name__}")


class TextPresentationSink(PresentationSink):
    """把结构化展示数据输出为人类可读文本。"""

    def __init__(self, state: TextOutputState) -> None:
        """绑定展示事件使用的文本输出状态。"""
        self.state = state

    async def emit(self, view: PresentationView) -> None:
        """输出一项结构化展示。"""
        if isinstance(view, RunStartedView):
            self.state.process(f"{const.APP_DESC} v{const.APP_VERSION}\n")
            self.state.process("--------\n")
            self.state.metadata("workdir", view.workdir)
            self.state.metadata("model", view.model)
            self.state.metadata("provider", view.provider)
            self.state.metadata("approval", view.approval)
            self.state.metadata("sandbox", view.sandbox)
            self.state.metadata("reasoning effort", view.reasoning_effort)
            self.state.metadata(
                "reasoning summaries",
                view.reasoning_summaries,
            )
            self.state.metadata("session id", view.session_id)
            self.state.process("--------\n")
            self.state.process("user\n", style=ANSI_CYAN)
            self.state.process(f"{view.message}\n")
            return None
        if isinstance(view, RunCompletedView):
            self.state.settle_assistant()
            return None
        if isinstance(view, RunIncompleteView):
            self.state.settle_assistant()
            reason = view.reason or "The response is incomplete"
            self.state.process(f"INCOMPLETE:\n{reason}\n")
            return None
        if isinstance(view, ToolStartView):
            return None
        if isinstance(view, NativeToolResultView):
            self._native_result(view)
            return None
        if isinstance(view, GenericToolResultView):
            status = "succeeded" if view.ok else "failed"
            self.state.process(_tool_name(view.name), style=ANSI_CYAN)
            self.state.process(f" {status}:\n{view.text}\n")
            return None
        if isinstance(view, FailureView):
            self.state.settle_assistant()
            self.state.process(f"ERROR:\n{view.error}\n")
            return None
        if isinstance(view, LifecycleView):
            self.state.process(f"{view.text}\n")
            return None
        if isinstance(view, ProgressView):
            self.state.process(_tool_name(view.tool_name), style=ANSI_CYAN)
            self.state.process(f"\n{view.text}\n")
            return None
        if isinstance(view, ApprovalView):
            self.state.process(f"{render_approval_view(view).plain_text}\n")
            return None
        if isinstance(view, HookRunView):
            self.state.process(f"{render_hook_run_view(view).plain_text}\n")
            return None
        if isinstance(view, PlanStepsStartView):
            self.state.process(f"todo: {view.step_count} steps\n")
            return None
        if isinstance(view, PlanUpdateView):
            self.state.process("todo:\n" + "\n".join(
                f"{item.status}: {item.step}" for item in view.items
            ) + "\n")
            return None
        if isinstance(view, BatchStartView):
            self.state.process(f"batch ({len(view.calls)} calls)\n")
            return None
        if isinstance(view, BatchCompletedView):
            self.state.process(f"batch completed ({len(view.results)} results)\n")
            return None

        raise TypeError(f"Unsupported presentation view: {type(view).__name__}")

    def _native_result(self, view: NativeToolResultView) -> None:
        """输出原生 coding 工具结果。"""
        data = _payload(view.data)
        if view.name == "apply_patch":
            label = "completed" if view.ok else "failed"
            self.state.process(f"patch: {label}\n")
            for item in data.get("files", []) if isinstance(data.get("files"), list) else []:
                if isinstance(item, dict) and item.get("path"):
                    self.state.process(f"{item.get('path')}\n")
            return None

        elapsed = max(0, int(view.cost_ms or 0))
        if view.ok:
            status = f" succeeded in {elapsed}ms:"
        else:
            exit_code = data.get("exit_code")

            status = (
                f" exited {exit_code} in {elapsed}ms:"
                if exit_code is not None
                else f" failed in {elapsed}ms:"
            )

        self.state.process(status + "\n")

        output = _tool_output(data)
        if output:
            self.state.process(output + "\n")


def create_text_output_session(
    log_file: str,
    *,
    animate: bool = True
) -> OutputSession:
    """创建无动画的人类可读文本输出会话。"""
    _ = animate
    state = TextOutputState(
        record_writer=StreamRecordWriter(log_file),
        stdout=sys.stdout,
        stderr=sys.stderr,
        color=_supports_color(sys.stderr),
    )

    control = TextOutputControl(state)

    return OutputSession(
        control=control,
        status=control,
        content=TextContentSink(state),
        presentation=TextPresentationSink(state),
        show_hook_lifecycle=True,
    )


if __name__ == '__main__':
    pass
