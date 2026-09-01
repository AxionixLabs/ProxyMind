# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

__all__ = ("ApprovalLedger", "ApprovalLedgerState")

ApprovalLedgerState: typing.TypeAlias = typing.Literal[
    "approved",
    "consumed",
    "unknown",
    "terminal",
]


@typing.runtime_checkable
class ApprovalLedger(typing.Protocol):
    """保存一次 Turn 内审批调用的消费和终态事实。"""

    def record_approved(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> None:
        """记录已获批准的工具调用。"""
        ...

    def discard(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> None:
        """移除未消费的审批事实。"""
        ...

    def record_terminal(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> None:
        """记录不可重新打开的终态审批。"""
        ...

    def is_terminal(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> bool:
        """判断审批是否已经收束。"""
        ...

    def consume(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> ApprovalLedgerState:
        """消费一次工具调用的审批状态。"""
        ...

    def is_approved(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> bool:
        """判断调用是否有尚未消费的批准。"""
        ...

    def clear_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> None:
        """清除一个 Turn 的审批状态。"""
        ...


if __name__ == "__main__":
    pass
