# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import math
import typing
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
    LifecycleView,
    NativeToolResultView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
    RunStartedView,
    ToolStartView
)
from ..stream_io.output_record import StreamRecordWriter
from .content import (
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


def _plain(value: typing.Any) -> typing.Any:
    """把值转换为 JSON 可编码的基础结构。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]

    return str(value)


def _data_payload(data: typing.Any) -> dict[str, typing.Any]:
    """提取工具结果字典。"""
    return data if isinstance(data, dict) else {}


def _command(arguments: dict[str, typing.Any], data: dict[str, typing.Any]) -> str:
    """提取命令文本。"""
    return str(data.get("command") or arguments.get("command") or arguments.get("cmd") or "")


def _aggregated_output(data: dict[str, typing.Any]) -> str:
    """提取命令执行的聚合输出。"""
    combined = data.get("output")
    if combined is not None and str(combined):
        return str(combined)

    return "\n".join(
        str(data[key]) for key in ("stdout", "stderr")
        if data.get(key) is not None and str(data[key])
    )


def _tool_item_type(name: str) -> str:
    """根据工具名称选择结构化项目类型。"""
    normalized = str(name or "").strip().lower()
    if normalized in {"web_search", "search_query"} or normalized.startswith("web_"):
        return "web_search"
    if normalized.startswith("collab"):
        return "collab_tool_call"

    return "mcp_tool_call"


class JsonOutputState:
    """保存逐行结构化输出状态。"""

    def __init__(
        self,
        record_writer: StreamRecordWriter,
        stdout: typing.TextIO
    ) -> None:
        self.record_writer = record_writer
        self.stdout        = stdout

        self.next_item: int                     = 0
        self.preferred_item_ids: dict[str, str] = {}
        self.reserved_item_ids: set[str]        = set()

        self._assistant_parts: list[str] = []

    async def open(self) -> None:
        """打开输出记录。"""
        await self.record_writer.open()

    async def close(self) -> None:
        """关闭输出记录。"""
        await self.record_writer.close()

    def item_id(self, preferred: str = "") -> str:
        """返回稳定的事件项目 ID。"""
        if preferred:
            if preferred in self.preferred_item_ids:
                return self.preferred_item_ids[preferred]

            value = preferred
            if value in self.reserved_item_ids:
                value = self._next_item_id()
            else:
                self.reserved_item_ids.add(value)
            self.preferred_item_ids[preferred] = value
            return value

        return self._next_item_id()

    def _next_item_id(self) -> str:
        """生成尚未使用的项目 ID。"""
        while True:
            value = f"item_{self.next_item}"
            self.next_item += 1
            if value not in self.reserved_item_ids:
                self.reserved_item_ids.add(value)
                return value

    def emit(self, event: dict[str, typing.Any]) -> None:
        """写出一行 JSON 事件。"""
        line = json.dumps(
            _plain(event),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        self.stdout.write(line + "\n")
        self.stdout.flush()
        self.record_writer.write_raw(line + "\n")

    def append_assistant(self, text: str) -> None:
        """追加一段 assistant 正文。"""
        if text:
            self._assistant_parts.append(str(text))

    def flush_assistant(self) -> None:
        """把 assistant 增量合并为一个完成项目。"""
        if not self._assistant_parts:
            return None

        text = "".join(self._assistant_parts)

        self._assistant_parts.clear()

        self.emit({
            "type": "item.completed",
            "item": {
                "id": self.item_id(),
                "type": "agent_message",
                "text": text,
            },
        })


class JsonOutputControl(OutputControlPort, OutputStatusPort):
    """提供逐行结构化事件的输出控制。"""

    def __init__(self, state: JsonOutputState) -> None:
        self.state = state

    async def open(self) -> None:
        """打开结构化输出。"""
        await self.state.open()

    async def stop(self, *, blink: bool = True) -> None:
        """停止结构化输出。"""
        _ = blink
        self.state.flush_assistant()
        await self.state.close()

    async def prepare_external_output(self) -> None:
        """在外部事件前收束 assistant 文本。"""
        self.state.flush_assistant()

    async def begin_tool_status(self) -> None:
        """忽略动态工具状态。"""
        return None

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """忽略自定义动态状态。"""
        _ = text
        return None

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None
    ) -> None:
        """忽略回复等待状态。"""
        _ = text, delay_sec, animate_after_sec
        return None

    async def end_status(self, *, immediate: bool = False) -> None:
        """结束当前输出状态。"""
        _ = immediate
        return None

    async def settle_stream(self) -> None:
        """收束 assistant 文本项目。"""
        self.state.flush_assistant()

    async def record_hidden_output(self, text: str) -> None:
        """忽略不直接展示的审计文本。"""
        _ = text
        return None

    def mark_stream_boundary(self) -> None:
        """标记正文输出边界。"""
        return None

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None
    ) -> None:
        """写出工具项目开始事件。"""
        self.state.flush_assistant()

        tool    = str(name or "tool")
        args    = dict(arguments) if isinstance(arguments, dict) else {}
        item_id = self.state.item_id(str(call_id or ""))

        if tool in {"shell_command", "exec_command", "write_stdin"}:
            item = {
                "id": item_id,
                "type": "command_execution",
                "command": str(args.get("command") or args.get("cmd") or tool),
                "aggregated_output": "",
                "exit_code": None,
                "status": "in_progress",
            }
        elif tool == "apply_patch":
            item = {
                "id": item_id,
                "type": "file_change",
                "patch": str(args.get("patch") or ""),
                "status": "in_progress",
            }
        else:
            item = {
                "id": item_id,
                "type": _tool_item_type(tool),
                "name": tool,
                "arguments": args,
                "status": "in_progress",
            }
        self.state.emit({"type": "item.started", "item": item})

    def flush(self) -> None:
        """刷新输出记录。"""
        self.state.record_writer.flush()


class JsonContentSink(ContentSink):
    """把正文增量合并为完整消息事件。"""

    def __init__(self, state: JsonOutputState) -> None:
        self.state = state

    async def emit(self, output: ContentOutput) -> None:
        """接收正文增量。"""
        if isinstance(output, AssistantTextDelta):
            self.state.append_assistant(output.text)
            return None
        if isinstance(output, SourcesOutput):
            return None
        raise TypeError(f"Unsupported content output: {type(output).__name__}")


class JsonPresentationSink(PresentationSink):
    """把展示数据编码为逐行结构化事件。"""

    def __init__(self, state: JsonOutputState) -> None:
        self.state = state

    async def emit(self, view: PresentationView) -> None:
        """写出一项结构化展示事件。"""
        if isinstance(view, RunStartedView):
            thread_id = view.thread_id or "thread_unknown"
            self.state.emit({"type": "thread.started", "thread_id": thread_id})
            self.state.emit({"type": "turn.started"})
            return None
        if isinstance(view, RunCompletedView):
            self.state.flush_assistant()
            self.state.emit({"type": "turn.completed", "usage": view.usage})
            return None
        if isinstance(view, FailureView):
            self.state.flush_assistant()
            self.state.emit({"type": "turn.failed", "error": view.error, "phase": view.phase})
            return None
        if isinstance(view, ToolStartView):
            return None
        if isinstance(view, NativeToolResultView):
            self._native_result(view)
            return None
        if isinstance(view, GenericToolResultView):
            self._item_completed(view.call_id, {
                "type": _tool_item_type(view.name),
                "name": view.name,
                "text": view.text,
                "status": "completed" if view.ok else "failed",
            })
            return None
        if isinstance(view, LifecycleView):
            self._message_completed(view.text)
            return None
        if isinstance(view, ProgressView):
            self._message_completed(view.text)
            return None
        if isinstance(view, ApprovalView):
            self._item_completed("", {
                "type": "mcp_tool_call",
                "name": "approval",
                "decision": view.decision,
                "status": view.state,
                "source": view.source,
            })
            return None
        if isinstance(view, PlanStepsStartView):
            self._item_completed("", {
                "type": "todo_list",
                "step_count": view.step_count,
                "tools": list(view.tools),
                "status": "in_progress",
            })
            return None
        if isinstance(view, PlanUpdateView):
            self._item_completed("", {
                "type": "todo_list",
                "items": [
                    {"step": item.step, "status": item.status}
                    for item in view.items
                ],
                "status": "completed",
            })
            return None
        if isinstance(view, BatchStartView):
            self._item_completed("", {
                "type": "mcp_tool_call",
                "name": "batch",
                "calls": [
                    {"name": call.name, "arguments": call.arguments}
                    for call in view.calls
                ],
                "status": "in_progress",
            })
            return None
        if isinstance(view, BatchCompletedView):
            self._item_completed("", {
                "type": "mcp_tool_call",
                "name": "batch",
                "results": [
                    {"name": result.name, "text": result.text, "ok": result.ok}
                    for result in view.results
                ],
                "status": "completed",
            })
            return None
        raise TypeError(f"Unsupported presentation view: {type(view).__name__}")

    def _item_completed(self, item_id: str, item: dict[str, typing.Any]) -> None:
        """写出 item.completed。"""
        item = {"id": self.state.item_id(item_id), **item}
        self.state.emit({"type": "item.completed", "item": item})

    def _message_completed(self, text: str) -> None:
        """写出过程消息项目。"""
        self._item_completed("", {"type": "agent_message", "text": str(text or "")})

    def _native_result(self, view: NativeToolResultView) -> None:
        """编码原生 coding 工具结果。"""
        data = _data_payload(view.data)
        if view.name == "apply_patch":
            item = {
                "type"   : "file_change",
                "status" : "completed" if view.ok else "failed",
                "patch"  : str(view.arguments.get("patch") or ""),
                "files"  : data.get("files", []),
            }
        else:
            item = {
                "type"              : "command_execution",
                "command"           : _command(view.arguments, data),
                "aggregated_output" : _aggregated_output(data),
                "exit_code"         : data.get("exit_code") if not view.ok else data.get("exit_code", 0),
                "status"            : "completed" if view.ok else "failed",
            }
        self._item_completed(view.call_id, item)


def create_json_output_session(
    log_file: str,
    *,
    animate: bool = True,
) -> OutputSession:
    """创建逐行结构化输出会话。"""
    _ = animate
    state = JsonOutputState(
        record_writer=StreamRecordWriter(log_file),
        stdout=sys.stdout,
    )
    control = JsonOutputControl(state)
    return OutputSession(
        control=control,
        status=control,
        content=JsonContentSink(state),
        presentation=JsonPresentationSink(state),
    )


if __name__ == '__main__':
    pass
