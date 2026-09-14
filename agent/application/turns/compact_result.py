# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.domain.hooks import (
    CompactOutcome,
    CompactResultSource,
    CompactTriggerReason,
    CompactTriggerSource,
)


@dataclass(frozen=True, slots=True)
class CompactResult:
    """描述一次上下文压缩的稳定结果值对象。"""
    outcome: CompactOutcome
    message: str
    event: "CompactEvent | None" = None
    summary: str = ""
    transcript_path: str = ""
    trigger: CompactTriggerReason = "manual"
    trigger_source: CompactTriggerSource = "client"
    result_source: CompactResultSource = "fallback"
    continue_execution: bool = True
    continuation_message: str = ""

    @property
    def before_items(self) -> int | None:
        """返回服务端确认的压缩前条目数。"""
        return self.event.before_items if self.event is not None else None

    @property
    def after_items(self) -> int | None:
        """返回服务端确认的压缩后条目数。"""
        return self.event.after_items if self.event is not None else None

    @property
    def latency_ms(self) -> int | None:
        """返回服务端确认的压缩耗时，未知值保持为空。"""
        return self.event.latency_ms if self.event is not None else None

    @property
    def ok(self) -> bool:
        """返回压缩完成后是否允许继续执行。"""
        return self.outcome == "completed" and self.continue_execution


@dataclass(frozen=True, slots=True)
class CompactEvent:
    """描述协议适配器归一化后的会话压缩进度或终态。"""

    status: typing.Literal["started", "completed", "failed"]
    message: str = ""
    cid: str = ""
    sid: str = ""
    turn_id: str = ""
    item_id: str = ""
    event_seq: int | None = None
    presentation_epoch: int = 0
    phase: typing.Literal["pre_turn", "mid_turn", "standalone"] = "standalone"
    trigger: typing.Literal["automatic", "manual"] = "manual"
    reason: str = ""
    error_type: str | None = None
    retryable: bool | None = None
    before_items: int | None = None
    after_items: int | None = None
    before_chars: int | None = None
    after_chars: int | None = None
    dropped_items: int | None = None
    reduction_ratio: float | None = None
    latency_ms: int | None = None
    replacement_version: int | None = None


def compact_failure_message(error_type: str = "", *, status_code: int = 0) -> str:
    """把压缩错误类别转换为所有客户端路径一致使用的简短原因。"""
    if error_type == "empty_history" or status_code == 404:
        return "There is no conversation history to compact."
    if error_type == "not_compactable":
        return "A tool call is still running. Try again after it finishes."
    if error_type == "summary_failed":
        return "Could not generate a context summary."
    if error_type == "snapshot_failed":
        return "Could not read the conversation context."
    if error_type == "no_gain":
        return "Compaction did not reduce the context."
    if error_type == "cas_conflict":
        return "Conversation changed while compacting. Please try again."
    if error_type == "persist_failed":
        return "Failed to save the compacted context. Please try again."
    if status_code == 409:
        return "Conversation is busy or changed. Try /compact again after the current operation finishes."
    return "Context compaction failed. Please try again."


if __name__ == '__main__':
    pass
