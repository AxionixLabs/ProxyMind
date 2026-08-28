# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
)
from mind_nova.stream_events import (
    TurnDoneEvent,
    TurnFailedEvent,
    TurnTerminalEvent,
)
from .result import (
    RunResult,
    RunStatus,
)


_STATUS_PRIORITY: tuple[RunStatus, ...] = (
    "interrupted",
    "reconciliation_required",
    "failed",
    "completed",
    "incomplete",
)


@dataclass(slots=True)
class StreamTurnOutcome:
    """作为旧流式管线的唯一终态写者并生成稳定运行结果。"""

    usage: dict[str, typing.Any] = field(default_factory=dict)
    terminal_meta: dict[str, typing.Any] = field(default_factory=dict)
    can_continue: bool = False
    error: str | None = None
    additional_context: tuple[str, ...] = ()
    _terminal_statuses: set[RunStatus] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    @property
    def status(self) -> RunStatus:
        """按中断、对账、失败、完成和未完整的优先级返回终态。"""
        for status in _STATUS_PRIORITY:
            if status in self._terminal_statuses:
                return status
        return "incomplete"

    @property
    def is_interrupted(self) -> bool:
        """返回执行是否收到本地或远端中断。"""
        return "interrupted" in self._terminal_statuses

    @property
    def is_completed(self) -> bool:
        """返回执行是否收到完成终态。"""
        return "completed" in self._terminal_statuses

    @property
    def is_incomplete(self) -> bool:
        """返回执行是否收到未完整终态。"""
        return "incomplete" in self._terminal_statuses

    @property
    def is_failed(self) -> bool:
        """返回执行是否收到或产生失败终态。"""
        return "failed" in self._terminal_statuses

    @property
    def is_reconciliation_required(self) -> bool:
        """返回执行是否需要人工或恢复流程完成效果对账。"""
        return "reconciliation_required" in self._terminal_statuses

    @property
    def has_terminal_status(self) -> bool:
        """返回执行是否已经收到或产生任一明确终态。"""
        return bool(self._terminal_statuses)

    @property
    def continuation_allowed(self) -> bool:
        """返回停止 Hook 是否可以创建后续模型轮次。"""
        return self.status == "completed" or (
            self.status == "incomplete" and self.can_continue
        )

    @property
    def observation_outcome(self) -> str:
        """返回兼容现有观测事件的终态名称。"""
        return "complete" if self.status == "completed" else self.status

    def record_failed_event(self, event: TurnFailedEvent) -> None:
        """合并服务端失败终态及其用量和响应元数据。"""
        self._terminal_statuses.add("failed")
        self.error = event.error
        self._record_terminal(event)

    def record_done_event(self, event: TurnDoneEvent) -> None:
        """合并服务端正常、未完整或中断终态。"""
        self._terminal_statuses.add(event.status)
        self.can_continue = event.can_continue is True
        if event.status == "incomplete":
            self.error = event.reason or None
        self._record_terminal(event)

    def interrupt(self, error: str | None = None) -> None:
        """记录本地取消或工具处理产生的中断。"""
        self._terminal_statuses.add("interrupted")
        if error is not None:
            self.error = error

    def require_reconciliation(self, error: str) -> None:
        """记录无法确定或无法提交的持久效果结果。"""
        self._terminal_statuses.add("reconciliation_required")
        self.error = str(error)

    def fail(
        self,
        error: str,
        *,
        additional_context: typing.Iterable[str] = (),
    ) -> None:
        """记录本地失败及需要交还上层会话的上下文。"""
        self._terminal_statuses.add("failed")
        self.error = str(error)
        self.additional_context = (
            (additional_context,)
            if isinstance(additional_context, str)
            else tuple(additional_context)
        )

    def settle_stream(self) -> None:
        """在事件流正常结束但没有终态时固定未完整原因。"""
        if not self.has_terminal_status:
            self.error = "stream ended before turn completion"

    def build_result(self, assistant_text: str) -> RunResult:
        """从当前终态快照构建不可变运行结果。"""
        return RunResult(
            status=self.status,
            assistant_text=assistant_text,
            usage=dict(self.usage),
            error=self.error,
            additional_context=self.additional_context,
            **self.terminal_meta,
        )

    def _record_terminal(self, event: TurnTerminalEvent) -> None:
        """替换最近一次协议终态携带的用量和响应元数据。"""
        self.usage = dict(event.usage)
        self.terminal_meta = _terminal_metadata(event)


def _terminal_metadata(event: TurnTerminalEvent) -> dict[str, typing.Any]:
    """提取需要保留到运行结果和会话记录的终态字段。"""
    fields: dict[str, typing.Any] = {}
    for field_name in (
        "response_id",
        "model",
        "route",
        "request_id",
        "service_tier",
        "stop_reason",
        "stop_sequence",
    ):
        value = getattr(event, field_name)
        if value not in {None, ""}:
            fields[field_name] = value
    if isinstance(event, TurnDoneEvent):
        if event.reason:
            fields["reason"] = event.reason
        if event.can_continue is not None:
            fields["can_continue"] = event.can_continue
    return fields


if __name__ == '__main__':
    pass
