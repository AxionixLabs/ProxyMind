# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
import threading
from dataclasses import dataclass

ApprovalLedgerState: typing.TypeAlias = typing.Literal[
    "approved",
    "consumed",
    "unknown",
    "terminal",
]

ToolResultState: typing.TypeAlias = typing.Literal[
    "pending",
    "committed",
]


@dataclass(frozen=True, slots=True)
class ToolResultRecord(object):
    """保存一次客户端工具结果及其提交状态。"""

    name: str
    ok: bool
    result: typing.Any
    arguments: dict[str, typing.Any]
    additional_context: tuple[str, ...]
    state: ToolResultState = "pending"


class ApprovalCallLedger(object):
    """维护审批请求与后续工具调用及结果提交的本地生命周期。"""

    def __init__(self) -> None:
        """创建线程安全的调用审批账本。"""
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str, str, str], ApprovalLedgerState] = {}
        self._results: dict[tuple[str, str, str, str], ToolResultRecord] = {}

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

    def record_result_pending(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
        name: str,
        ok: bool,
        result: typing.Any,
        arguments: typing.Mapping[str, typing.Any] | None,
        additional_context: typing.Iterable[str] = (),
    ) -> None:
        """保存已执行但尚未确认提交的工具结果。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        record = ToolResultRecord(
            name=str(name or ""),
            ok=bool(ok),
            result=copy.deepcopy(result),
            arguments=copy.deepcopy(dict(arguments or {})),
            additional_context=tuple(
                str(value).strip()
                for value in additional_context
                if str(value).strip()
            ),
        )
        with self._lock:
            self._results[key] = record

    def result_for(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> ToolResultRecord | None:
        """读取调用已经保存的工具结果。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            record = self._results.get(key)
            return copy.deepcopy(record) if record is not None else None

    def mark_result_committed(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
    ) -> None:
        """把已经收到服务端成功响应的工具结果标记为已提交。"""
        key = self._key(cid=cid, sid=sid, turn_id=turn_id, call_id=call_id)
        with self._lock:
            record = self._results.get(key)
            if record is not None:
                self._results[key] = ToolResultRecord(
                    name=record.name,
                    ok=record.ok,
                    result=copy.deepcopy(record.result),
                    arguments=copy.deepcopy(record.arguments),
                    additional_context=record.additional_context,
                    state="committed",
                )

    def clear_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        preserve_pending_results: bool = False,
    ) -> None:
        """清除一个逻辑 turn 的审批状态及已收束结果。"""
        prefix = (str(cid or ""), str(sid or ""), str(turn_id or ""))
        with self._lock:
            for key in tuple(self._states):
                if key[:3] == prefix:
                    del self._states[key]
            for key, record in tuple(self._results.items()):
                if key[:3] != prefix:
                    continue
                if preserve_pending_results and record.state == "pending":
                    continue
                del self._results[key]


if __name__ == "__main__":
    pass
