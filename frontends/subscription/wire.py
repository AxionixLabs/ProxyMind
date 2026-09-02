# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import uuid


def now_ts() -> int:
    """返回当前的 Unix 秒级时间戳。"""
    return int(time.time())


def new_message_id(prefix: str) -> str:
    """为订阅客户端生成可追踪的消息 ID。"""
    return f"msg_{prefix}_{uuid.uuid4().hex[:12]}"


def build_envelope(
    message_type: str,
    session_id: str,
    *,
    payload: dict[str, typing.Any] | None = None,
    cid: str | None = None,
    sid: str | None = None,
    message_id: str | None = None,
    seq: int | None = None
) -> dict[str, typing.Any]:
    """构造包含通用顶层字段的协议信封。"""
    envelope: dict[str, typing.Any] = {
        "type": message_type,
        "session_id": session_id,
        "message_id": message_id or new_message_id(message_type.replace(".", "_")),
        "ts": now_ts(),
        "payload": payload or {}
    }

    if seq is not None:
        envelope["seq"] = seq
    if cid:
        envelope["cid"] = cid
    if sid:
        envelope["sid"] = sid

    return envelope


def ensure_ws_base(base_url: str) -> str:
    """把 HTTP(S) 基础地址转换成 WS(S) 基础地址。"""
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://"):].rstrip("/")
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://"):].rstrip("/")
    return base_url.rstrip("/")


if __name__ == '__main__':
    pass
