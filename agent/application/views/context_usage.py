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


@dataclass(frozen=True, slots=True)
class SessionExitSnapshot:
    """根会话在清理前冻结的退出事实，由统一收尾消费一次，不持有存储或运行时。"""

    cid: str
    sid: str
    disposition: typing.Literal["recoverable", "archived", "deleted", "pending_delete"]
    record: ContextUsageRecord | None
    remote_stop_confirmed: bool
    deletion_request_id: str | None = None

    def __post_init__(self) -> None:
        """保证快照身份、用量归属和未决删除恢复身份一致。"""
        if not self.cid or not self.sid:
            raise ValueError("exit snapshot requires a session identity")
        if self.record is not None and (self.record.cid, self.record.sid) != (self.cid, self.sid):
            raise ValueError("exit usage must belong to the same session")
        if self.disposition not in ("recoverable", "archived", "deleted", "pending_delete"):
            raise ValueError("invalid exit disposition")
        if (self.disposition == "pending_delete") != bool(self.deletion_request_id):
            raise ValueError("only pending deletion requires a recovery request")


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
