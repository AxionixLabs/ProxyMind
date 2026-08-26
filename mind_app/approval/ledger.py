# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import threading

ApprovalLedgerState: typing.TypeAlias = typing.Literal[
    "approved",
    "consumed",
    "unknown",
]


class ApprovalCallLedger(object):
    """维护远端审批与后续工具调用之间的本地生命周期关联。"""

    def __init__(self) -> None:
        """创建线程安全的调用审批账本。"""
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str, str, str], ApprovalLedgerState] = {}

    @staticmethod
    def _key(
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> tuple[str, str, str, str]:
        """规范化账本键。"""
        values = tuple(
            str(value or "").strip()
            for value in (cid, sid, turn_id, call_id)
        )
        if not all(values):
            raise ValueError("approval ledger key is incomplete")
        return typing.cast(tuple[str, str, str, str], values)

    def record_approved(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> None:
        """记录一个已由服务端审批通过的调用。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            self._states[key] = "approved"

    def discard(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> None:
        """移除尚未消费的调用审批记录。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            self._states.pop(key, None)

    def consume(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> ApprovalLedgerState:
        """消费一次工具调用并返回其审批状态。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            state = self._states.get(key, "unknown")
            if state == "approved":
                self._states[key] = "consumed"
            return state

    def clear_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str
    ) -> None:
        """清除一个逻辑 turn 的全部调用审批状态。"""
        prefix = (str(cid or ""), str(sid or ""), str(turn_id or ""))
        with self._lock:
            for key in tuple(self._states):
                if key[:3] == prefix:
                    del self._states[key]


if __name__ == "__main__":
    pass
