# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.protocol.context_usage import ContextUsageRecord

CONTEXT_BASELINE_TOKENS = 12_000


@dataclass(frozen=True, slots=True)
class ContextUsageView:
    """描述根会话的用量展示；pending 和 unknown 均不隐含零占用。"""

    status: typing.Literal["initial", "pending", "known", "unknown"]
    record: ContextUsageRecord | None = None


def context_remaining_percent(record: ContextUsageRecord) -> int | None:
    """按最近占用和最终窗口计算基线校正后的非负四舍五入百分比。"""
    window = record.model_context_window
    total = record.last_total_tokens
    if window is None or total is None:
        return None
    available = window - CONTEXT_BASELINE_TOKENS
    if available <= 0:
        return 0
    used = max(total - CONTEXT_BASELINE_TOKENS, 0)
    remaining = max(available - used, 0)
    return min(100, (200 * remaining + available) // (2 * available))


if __name__ == '__main__':
    pass
