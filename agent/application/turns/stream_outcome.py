# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)
from .run_result import (
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


class _TerminalEvent(typing.Protocol):
    """定义结果聚合所需的终态字段；协议适配器负责提供已校验事件。"""

    response_id: str
    model: str
    route: str
    request_id: str
    service_tier: str
    usage: Mapping[str, typing.Any]
    stop_reason: str | None
    stop_sequence: str | None


class _FailedEvent(_TerminalEvent, typing.Protocol):
    """定义失败终态事件的错误字段。"""

    error: str
    error_type: str
    error_source: str
    status_code: int | None
    retryable: bool | None


class _DoneEvent(_TerminalEvent, typing.Protocol):
    """定义正常终态事件的续跑字段。"""

    status: RunStatus
    reason: str
    can_continue: bool | None


@dataclass(slots=True)
class StreamTurnOutcome:
    """作为旧流式管线的唯一终态写者并生成稳定运行结果。"""

    usage: dict[str, typing.Any] = field(default_factory=dict)
    terminal_meta: dict[str, typing.Any] = field(default_factory=dict)
    can_continue: bool = False
    error: str | None = None
    additional_context: tuple[str, ...] = ()
    error_code: str | None = None
    error_details: dict[str, typing.Any] = field(default_factory=dict)
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

    def record_failed_event(self, event: _FailedEvent) -> None:
        """合并服务端失败终态及其用量和响应元数据。"""
        self._terminal_statuses.add("failed")
        self.error = event.error
        self.error_code = event.error_type or None
        self.error_details = {
            key: value
            for key, value in {
                "source": event.error_source,
                "status_code": event.status_code,
                "retryable": event.retryable,
            }.items()
            if value not in {None, ""}
        }
        self._record_terminal(event)

    def record_done_event(self, event: _DoneEvent) -> None:
        """合并服务端正常、未完整或中断终态。"""
        self._terminal_statuses.add(event.status)
        self.can_continue = event.can_continue is True
        if event.status == "incomplete":
            self.error = event.reason or None
        self._record_terminal(
            event,
            reason=event.reason,
            can_continue=event.can_continue,
        )

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
        error_code: str | None = None,
        error_details: typing.Mapping[str, typing.Any] | None = None,
        additional_context: typing.Iterable[str] = (),
    ) -> None:
        """记录本地失败及需要交还上层会话的上下文。"""
        self._terminal_statuses.add("failed")
        self.error = str(error)
        if error_code not in {None, ""}:
            self.error_code = str(error_code)
        if error_details is not None:
            self.error_details = dict(error_details)
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
            error_code=self.error_code,
            error_details=dict(self.error_details),
            additional_context=self.additional_context,
            **self.terminal_meta,
        )

    def _record_terminal(
        self,
        event: _TerminalEvent,
        *,
        reason: str = "",
        can_continue: bool | None = None,
    ) -> None:
        """替换最近一次协议终态携带的用量和响应元数据。"""
        self.usage = dict(event.usage)
        self.terminal_meta = _terminal_metadata(
            event,
            reason=reason,
            can_continue=can_continue,
        )


def _terminal_metadata(
    event: _TerminalEvent,
    *,
    reason: str = "",
    can_continue: bool | None = None,
) -> dict[str, typing.Any]:
    """提取需要保留到运行结果和会话记录的终态字段。"""
    fields: dict[str, typing.Any] = {}
    for field_name, value in (
        ("response_id", event.response_id),
        ("model", event.model),
        ("route", event.route),
        ("request_id", event.request_id),
        ("service_tier", event.service_tier),
        ("stop_reason", event.stop_reason),
        ("stop_sequence", event.stop_sequence),
    ):
        if value not in {None, ""}:
            fields[field_name] = value
    if reason:
        fields["reason"] = reason
    if can_continue is not None:
        fields["can_continue"] = can_continue
    return fields


if __name__ == '__main__':
    pass
