# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SessionIdentity: typing.TypeAlias = tuple[str, str]


class SessionEventCursorStore(object):
    """持有每个远端 Session 已确认处理的持久事件水位。"""

    def __init__(self) -> None:
        """初始化空的会话水位集合。"""
        self._event_sequences: dict[SessionIdentity, int] = {}

    def current(self, *, cid: str, sid: str) -> int:
        """返回指定 Session 当前已确认的事件序号。"""
        identity = self._identity(cid=cid, sid=sid)
        return self._event_sequences.get(identity, 0)

    def advance(self, *, cid: str, sid: str, event_seq: int) -> int:
        """单调推进指定 Session 的事件水位并返回最终值。"""
        identity = self._identity(cid=cid, sid=sid)
        if (
            isinstance(event_seq, bool)
            or not isinstance(event_seq, int)
            or event_seq < 0
        ):
            raise ValueError("event_seq must be a non-negative integer")
        current = self._event_sequences.get(identity, 0)
        resolved = max(current, event_seq)
        self._event_sequences[identity] = resolved
        return resolved

    @staticmethod
    def _identity(*, cid: str, sid: str) -> SessionIdentity:
        """校验并返回稳定的远端 Session 身份。"""
        normalized_cid = str(cid or "").strip()
        normalized_sid = str(sid or "").strip()
        if not normalized_cid or not normalized_sid:
            raise ValueError("cid and sid are required")
        return normalized_cid, normalized_sid


if __name__ == '__main__':
    pass
