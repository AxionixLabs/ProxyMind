# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import threading
import typing

ApprovalLedgerState: typing.TypeAlias = typing.Literal[
    "approved",
    "consumed",
    "unknown",
    "terminal",
]


class ApprovalCallLedger(object):
    """维护审批请求与后续工具调用的本地生命周期。"""

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
        normalized_cid = str(cid or "").strip()
        normalized_sid = str(sid or "").strip()
        normalized_turn_id = str(turn_id or "").strip()
        normalized_call_id = str(call_id or "").strip()
        if not all((normalized_cid, normalized_sid, normalized_turn_id, normalized_call_id)):
            raise ValueError("approval ledger key is incomplete")
        return (
            normalized_cid,
            normalized_sid,
            normalized_turn_id,
            normalized_call_id,
        )

    def record_approved(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> None:
        """记录客户端已经确认的审批调用。"""
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
        """移除未确认的审批请求。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            self._states.pop(key, None)

    def record_terminal(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> None:
        """记录已收束且不应由旧事件重新打开的审批调用。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            self._states[key] = "terminal"

    def is_terminal(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> bool:
        """判断审批是否已经由快照或终态事件收束。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            return self._states.get(key) == "terminal"

    def consume(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str
    ) -> ApprovalLedgerState:
        """登记一次工具调用并返回其此前的审批状态。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            state = self._states.get(key, "unknown")
            if state in {"approved", "unknown"}:
                self._states[key] = "consumed"
            return state

    def is_approved(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> bool:
        """判断调用是否已有尚未消费的允许决定。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            return self._states.get(key) == "approved"

    def clear_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> None:
        """清除一个逻辑 turn 的审批状态。"""
        prefix = (str(cid or ""), str(sid or ""), str(turn_id or ""))
        with self._lock:
            for key in tuple(self._states):
                if key[:3] == prefix:
                    del self._states[key]


if __name__ == '__main__':
    pass
