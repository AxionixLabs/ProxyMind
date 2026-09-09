# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import dataclass

from protocol.schema.identifiers import (
    new_cid,
    new_sid,
    valid_session_ids,
)


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """描述一次会话轮次及其启动边界。"""
    cid: str
    sid: str
    turn_index: int
    session_started: bool
    session_mode: typing.Literal["create", "existing"] = "existing"
    start_reason: str = ""
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

    def metadata(self) -> dict[str, str]:
        """返回不包含本地生命周期状态的会话标识。"""
        return {"cid": self.cid, "sid": self.sid}


class ConversationState(object):
    """管理模型对话边界和会话标识。"""

    def __init__(
        self,
        *,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None,
        created_at: float = 0.0,
        turn_count: int = 0,
        reset_count: int = 0,
        reset_reason: str = "",
        start_reason: str = "",
        fork_source_available: bool = False
    ) -> None:
        self.cid = self._clean(cid)
        self.sid = self._clean(sid)

        if self.cid or self.sid:
            if not valid_session_ids(self.cid, self.sid):
                raise ValueError("valid cid and sid are required")

        self.created_at = float(created_at or 0.0)
        self.turn_count = int(turn_count or 0)
        self.reset_count = int(reset_count or 0)
        self.reset_reason = str(reset_reason or "")
        self.start_reason = str(start_reason or "").strip()
        self.session_bound = valid_session_ids(self.cid, self.sid)

        self.fork_source_available = bool(
            fork_source_available or self.turn_count > 0
        )

        self._pending_context: list[str] = []
        self._pending_system_messages: list[str] = []

    def begin_turn(
        self,
        *,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None,
        start_reason: str = ""
    ) -> ConversationTurn:
        """为新轮次初始化、续用或绑定会话标识。"""
        external_cid = self._clean(cid)
        external_sid = self._clean(sid)

        if external_cid or external_sid:
            if not valid_session_ids(external_cid, external_sid):
                raise ValueError("valid cid and sid are required")
            self._bind_external(
                external_cid,
                external_sid,
                start_reason=start_reason,
            )

        elif not self.cid or not self.sid:
            self.reset(reason="initial")

        session_mode: typing.Literal["create", "existing"] = (
            "existing" if self.fork_source_available else "create"
        )
        session_started = self.turn_count == 0
        boundary_reason = ""
        if session_started:
            boundary_reason = self.start_reason.strip() or "bound"

        self.turn_count += 1
        self.session_bound = True
        self.fork_source_available = True
        metadata = self.snapshot()

        additional_context, system_message = self.consume_turn_context()

        if session_started:
            self.start_reason = ""

        return ConversationTurn(
            cid=metadata["cid"],
            sid=metadata["sid"],
            turn_index=self.turn_count,
            session_started=session_started,
            session_mode=session_mode,
            start_reason=boundary_reason,
            additional_context=additional_context,
            system_message=system_message,
        )

    def reset(self, *, reason: str = "manual") -> dict[str, str]:
        """开始一个全新的对话。"""
        self.cid = new_cid()
        self.sid = new_sid(self.cid)

        self.created_at = time.time()
        self.turn_count = 0
        self.session_bound = False

        self.fork_source_available = False

        self.reset_count += 1

        self.reset_reason = str(reason or "").strip() or "manual"
        self.start_reason = self.reset_reason

        self._pending_context.clear()
        self._pending_system_messages.clear()

        return self.snapshot()

    def queue_turn_context(
        self,
        additional_context: typing.Iterable[str] = (),
        *,
        system_message: str = ""
    ) -> None:
        """追加下一轮模型请求使用的一次性上下文。"""
        for value in additional_context:
            text = str(value or "").strip()
            if text:
                self._pending_context.append(text)

        system_text = str(system_message or "").strip()
        if system_text:
            self._pending_system_messages.append(system_text)

    def consume_turn_context(self) -> tuple[tuple[str, ...], str]:
        """读取并清空下一轮模型请求的一次性上下文。"""
        contexts = tuple(self._pending_context)
        system_message = "\n\n".join(self._pending_system_messages)
        self._pending_context.clear()
        self._pending_system_messages.clear()
        return contexts, system_message

    def snapshot(self) -> dict[str, str]:
        """返回当前会话标识快照。"""
        created_session = not self.cid or not self.sid
        cid = self.cid or new_cid()
        sid = self.sid or new_sid(cid)

        self.cid = cid
        self.sid = sid

        if created_session and self.turn_count == 0 and not self.start_reason:
            self.start_reason = "initial"

        if not self.created_at:
            self.created_at = time.time()

        return {"cid": cid, "sid": sid}

    def metadata(self) -> dict[str, typing.Any]:
        """返回可用于调试或 UI 展示的对话状态。"""
        ids = self.snapshot()

        return {
            **ids,
            "created_at": self.created_at,
            "turn_count": self.turn_count,
            "reset_count": self.reset_count,
            "reset_reason": self.reset_reason
        }

    def _bind_external(
        self,
        cid: typing.Optional[str],
        sid: typing.Optional[str],
        *,
        start_reason: str
    ) -> None:
        """绑定外部请求带入的会话标识。"""
        if cid and (cid != self.cid or sid != self.sid):
            self.cid = cid
            self.sid = sid or new_sid(cid)

            self.created_at = time.time()
            self.turn_count = 0
            self.session_bound = True
            self.start_reason = str(start_reason or "").strip() or "external"

            self._pending_context.clear()
            self._pending_system_messages.clear()

            return None

        if cid:
            self.cid = cid
        elif not self.cid:
            self.cid = new_cid()

        self.sid = sid or self.sid or new_sid(self.cid)
        self.session_bound = True
        if not self.created_at:
            self.created_at = time.time()

    @staticmethod
    def _clean(value: typing.Optional[str]) -> typing.Optional[str]:
        """清理外部传入的标识字符串。"""
        text = str(value or "").strip()
        return text or None


if __name__ == '__main__':
    pass
