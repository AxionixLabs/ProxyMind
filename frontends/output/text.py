# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing

from agent.application.views import (
    ApprovalView,
    BatchCompletedView,
    BatchStartView,
    FailureView,
    GenericToolResultView,
    HookRunView,
    LifecycleView,
    NativeToolResultView,
    PatchView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
    ToolStartView,
)
from agent.application.views.contracts import (
    PresentationSink,
    PresentationView,
)
from agent.ports import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ContentOutput,
    ContentSink,
    OutputControlPort,
    OutputSession,
    OutputStatusPort,
    SourcesOutput,
)
from frontends.terminal.renderers.approval import render_approval_view
from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text,
)
from metadata import const
from .recording import StreamRecordWriter

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_WARNING = "\x1b[1;33m"
ANSI_CYAN = "\x1b[1;96m"
ANSI_MAGENTA = "\x1b[1;95m"


class TextStream(typing.Protocol):
    """定义文本输出只依赖的最小流能力。"""

    def write(self, text: str) -> int:
        """写入文本并返回已接收字符数。"""
        ...

    def flush(self) -> None:
        """刷新已写入内容。"""
        ...


def _line(value: typing.Any) -> str:
    """把值转换为单行文本。"""
    return sanitize_terminal_line(value)


def _write(stream: TextStream, text: str) -> None:
    """写入并刷新一个文本块。"""
    if text:
        stream.write(text)
        stream.flush()


def _terminal_text(value: typing.Any) -> str:
    """移除外部文本中的终端控制序列。"""
    return sanitize_terminal_text(value)


def _styled_text(text: str, style: str) -> str:
    """生成在末尾换行前复位的 ANSI 文本。"""
    body = text.rstrip("\r\n")
    trailing = text[len(body):]

    if not body:
        return trailing

    return f"{style}{body}{ANSI_RESET}{trailing}"


def _supports_color(stream: typing.TextIO) -> bool:
    """判断输出流是否适合写入 ANSI 样式。"""
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR") not in {None, "", "0"}:
        return True

    return stream.isatty()


def _tool_name(name: str) -> str:
    """返回适合终端展示的工具名称。"""
    return _line(name) or "tool"


def _payload(data: typing.Any) -> dict[str, typing.Any]:
    """提取工具结果中的字典载荷。"""
    return data if isinstance(data, dict) else {}


def _tool_output(data: typing.Any) -> str:
    """提取工具结果中的标准输出文本。"""
    payload = _payload(data)
    combined = payload.get("output")

    if combined is not None:
        combined_text = _line(combined).rstrip("\n")
        if combined_text:
            return combined_text

    values: list[str] = []
    for key in ("stdout", "stderr"):
        value = payload.get(key)
        if value is None:
            continue
        text = _line(value).rstrip("\n")
        if text:
            values.append(text)

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
        self.record_writer = record_writer
        self.stdout = stdout
        self.stderr = stderr
        self.color = color
        self.assistant_open = False
        self._assistant_parts: list[str] = []
        self._assistant_item_id: str = ""

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
        return None

    def hook(self, event: str, *, status: str | None = None) -> None:
        """输出命令执行形态的 Hook 生命周期。"""
        event_text = _line(event) or "Unknown"
        status_text = _line(status) if status is not None else ""
        suffix = f" {status_text}" if status_text else ""
        plain = f"hook: {event_text}{suffix}\n"

        if self.color:
            visible = (
                f"{ANSI_BOLD}hook:{ANSI_RESET} "
                f"{ANSI_DIM}{event_text}{ANSI_RESET}{suffix}\n"
            )
        else:
            visible = plain

        _write(self.stderr, visible)
        self.record_writer.write(plain, block=True)
        return None

    def warning(self, message: str) -> None:
        """输出命令执行形态的运行时告警。"""
        text = _line(message)
        if text:
            plain = f"warning: {text}\n"
            visible = (
                f"{ANSI_WARNING}warning:{ANSI_RESET} {text}\n"
                if self.color
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

        if plain:
            if not self.assistant_open:
                self.process(f"{const.APP_DESC.lower()}\n", style=ANSI_MAGENTA)
                self.assistant_open = True

            _write(self.stdout, plain)
            self.record_writer.write(plain)

    def append_assistant(self, text: str, *, item_id: str = "") -> None:
        """追加一段 assistant 正文。"""
        item_id = str(item_id or "").strip()
        if (
            item_id
            and self._assistant_item_id
            and self._assistant_item_id != item_id
        ):
            self.settle_assistant()
        if item_id:
            self._assistant_item_id = item_id
        if text:
            self._assistant_parts.append(str(text))

    def finalize_assistant(self, text: str | None, *, item_id: str = "") -> None:
        """在 assistant 段落结算前应用 provider 的最终正文。"""
        if text is None:
            return None
        item_id = str(item_id or "").strip()
        if (
            item_id
            and self._assistant_item_id
            and self._assistant_item_id != item_id
        ):
            return None
        self._assistant_parts = [str(text)]
        if item_id:
            self._assistant_item_id = item_id

    def flush_assistant(self) -> None:
        """一次输出当前 assistant 增量。"""
        if self._assistant_parts:
            text = "".join(self._assistant_parts)
            self._assistant_parts.clear()
            self._assistant_item_id = ""
            self.assistant(text)

    def settle_assistant(self) -> None:
        """结束一段 assistant 正文。"""
        self.flush_assistant()
        if self.assistant_open:
            if self.record_writer.trailing_newlines < 1:
                _write(self.stdout, "\n")
                self.record_writer.write("\n")
            self.assistant_open = False


class TextOutputControl(OutputControlPort, OutputStatusPort):
    """提供无动画的文本输出控制。"""

    def __init__(self, state: TextOutputState) -> None:
        """绑定共享的文本输出状态。"""
        self.state = state

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
        safe_call_id = sanitize_terminal_line(call_id)
        if tool == "write_stdin":
            if safe_call_id:
                self.state.record_writer.write_audit(
                    f"tool {tool} call_id={safe_call_id}"
                )
        else:
            args = arguments if isinstance(arguments, dict) else {}

            if tool in {"shell_command", "exec_command"}:
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

            if safe_call_id:
                self.state.record_writer.write_audit(
                    f"tool {tool} call_id={safe_call_id}"
                )

    def flush(self) -> None:
        """刷新文本记录。"""
        self.state.record_writer.flush()

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


class TextContentSink(ContentSink):
    """把结构化正文写入文本输出流。"""

    def __init__(self, state: TextOutputState) -> None:
        """绑定正文使用的文本输出状态。"""
        self.state = state
        self._completed_item_ids: set[tuple[str, str]] = set()

    async def emit(self, output: ContentOutput) -> None:
        """输出正文或忽略来源元数据。"""
        if isinstance(output, AssistantTextDelta):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            self.state.append_assistant(output.text, item_id=output.item_id)
            return None
        if isinstance(output, AssistantSegmentCompleted):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            if output.item_id:
                self._completed_item_ids.add(item_key)
            self.state.finalize_assistant(
                output.final_text,
                item_id=output.item_id,
            )
            self.state.settle_assistant()
            return None
        if isinstance(output, AssistantOutputBoundary):
            self.state.settle_assistant()
            return None
        if isinstance(output, (
                AssistantPresentationSuperseded,
                AssistantResponseSuperseded,
        )):
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

    def _native_result(self, view: NativeToolResultView) -> None:
        """输出原生 coding 工具结果。"""
        data = _payload(view.data)
        if view.name == "apply_patch":
            label = "completed" if view.ok else "failed"
            self.state.process(f"patch: {label}\n")
            for item in data.get("files", []) if isinstance(data.get("files"), list) else []:
                path = _line(item.get("path")) if isinstance(item, dict) else ""
                if path:
                    self.state.process(f"{path}\n")
        else:
            result_prefix = ""
            if view.name == "write_stdin":
                payload_session_id = _line(data.get("session_id"))
                argument_session_id = _line(view.arguments.get("session_id"))
                session_id = payload_session_id or argument_session_id
                suffix = f" {session_id}" if session_id else ""
                result_prefix = f"Wrote stdin{suffix}"

            elapsed = max(0, int(view.cost_ms or 0))
            if view.ok:
                status = f"{result_prefix} succeeded in {elapsed}ms:"
            else:
                exit_code = data.get("exit_code")
                exit_code_text = _line(exit_code) if exit_code is not None else ""

                status = (
                    f"{result_prefix} exited {exit_code_text} in {elapsed}ms:"
                    if exit_code_text
                    else f"{result_prefix} failed in {elapsed}ms:"
                )

            self.state.process(status + "\n")

            output = _tool_output(data)
            if output:
                self.state.process(output + "\n")

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
            for warning in view.hook_warnings:
                self.state.warning(warning)
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
        if isinstance(view, PatchView):
            if view.phase == "proposed":
                return None
            label = "completed" if view.phase == "applied" else "failed"
            self.state.process(f"patch: {label}\n")
            for file in view.files:
                path = file.new_path or file.old_path
                if path:
                    self.state.process(f"{path}\n")
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
            status = (
                None
                if view.phase == "started"
                else view.status.capitalize()
            )
            self.state.hook(view.event, status=status)
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
