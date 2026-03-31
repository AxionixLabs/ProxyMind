# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import uuid
import typing


DEFAULT_SHARED_SECRET = "Ck5OWbJCMwWQG4zDlRbeDs6kiTcqxo74gF8raVpy5cA="


def now_ts() -> int:
    """Return the current unix timestamp in seconds."""
    return int(time.time())


def new_message_id(prefix: str) -> str:
    """Build a traceable message id for the resident client."""
    return f"msg_{prefix}_{uuid.uuid4().hex[:12]}"


def build_envelope(
    message_type: str,
    session_id: str,
    *,
    payload: dict[str, typing.Any] | None = None,
    cid: str | None = None,
    sid: str | None = None,
    message_id: str | None = None,
    seq: int | None = None,
) -> dict[str, typing.Any]:
    """Create a protocol envelope with the common top-level fields."""
    envelope: dict[str, typing.Any] = {
        "type": message_type,
        "session_id": session_id,
        "message_id": message_id or new_message_id(message_type.replace(".", "_")),
        "ts": now_ts(),
        "payload": payload or {},
    }

    if seq is not None:
        envelope["seq"] = seq
    if cid:
        envelope["cid"] = cid
    if sid:
        envelope["sid"] = sid

    return envelope


def ensure_ws_base(base_url: str) -> str:
    """Convert an HTTP(S) base URL into a WS(S) base URL."""
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :].rstrip("/")
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :].rstrip("/")
    return base_url.rstrip("/")
