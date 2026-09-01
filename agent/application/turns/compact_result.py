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
    before_items: int | None = None
    after_items: int | None = None
    summary: str = ""
    transcript_path: str = ""
    trigger: CompactTriggerReason = "manual"
    trigger_source: CompactTriggerSource = "client"
    result_source: CompactResultSource = "fallback"
    continue_execution: bool = True

    @property
    def ok(self) -> bool:
        """返回上下文压缩是否完成。"""
        return self.outcome == "completed" and self.continue_execution


@dataclass(frozen=True, slots=True)
class CompactEvent:
    """描述协议适配器归一化后的会话压缩进度或终态。"""

    status: typing.Literal["started", "completed", "failed"]
    message: str = ""
    summary: str = ""
    before_items: int | None = None
    after_items: int | None = None


if __name__ == '__main__':
    pass
