# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mind_nova import craft


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
        reset_reason: str = ""
    ) -> None:
        self.cid = self._clean(cid)
        self.sid = self._clean(sid)

        self.created_at   = float(created_at or 0.0)
        self.turn_count   = int(turn_count or 0)
        self.reset_count  = int(reset_count or 0)
        self.reset_reason = str(reset_reason or "")

    def begin(
        self,
        *,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None
    ) -> dict[str, str]:
        """初始化、续用或绑定外部传入的会话标识。"""
        external_cid = self._clean(cid)
        external_sid = self._clean(sid)

        if external_cid or external_sid:
            self._bind_external(external_cid, external_sid)
        elif not self.cid or not self.sid:
            self.reset(reason="initial")

        self.turn_count += 1
        return self.snapshot()

    def reset(self, *, reason: str = "manual") -> dict[str, str]:
        """开始一个全新的对话。"""
        self.cid = craft.new_cid()
        self.sid = craft.new_sid(self.cid)

        self.created_at = time.time()
        self.turn_count = 0

        self.reset_count += 1

        self.reset_reason = str(reason or "manual")

        return self.snapshot()

    def snapshot(self) -> dict[str, str]:
        """返回当前会话标识快照。"""
        cid = self.cid or craft.new_cid()
        sid = self.sid or craft.new_sid(cid)

        self.cid = cid
        self.sid = sid

        if not self.created_at:
            self.created_at = time.time()

        return {"cid": cid, "sid": sid}

    def metadata(self) -> dict[str, typing.Any]:
        """返回可用于调试或 UI 展示的对话状态。"""
        ids = self.snapshot()

        return {
            **ids,
            "created_at"   : self.created_at,
            "turn_count"   : self.turn_count,
            "reset_count"  : self.reset_count,
            "reset_reason" : self.reset_reason
        }

    def _bind_external(
        self,
        cid: typing.Optional[str],
        sid: typing.Optional[str]
    ) -> None:
        """绑定外部请求带入的会话标识。"""
        if cid and cid != self.cid:
            self.cid = cid
            self.sid = sid or craft.new_sid(cid)
            self.created_at = time.time()
            self.turn_count = 0
            return None

        if cid:
            self.cid = cid
        elif not self.cid:
            self.cid = craft.new_cid()

        self.sid = sid or self.sid or craft.new_sid(self.cid)
        if not self.created_at:
            self.created_at = time.time()

    @staticmethod
    def _clean(value: typing.Optional[str]) -> typing.Optional[str]:
        """清理外部传入的标识字符串。"""
        text = str(value or "").strip()
        return text or None


if __name__ == '__main__':
    pass
