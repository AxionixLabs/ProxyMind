# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import uuid
import base64
import typing
import hashlib

TURN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
CID_RE = re.compile(r"^cid_([0-9a-z]+)_[0-9a-f]{8}$")
SID_RE = re.compile(r"^sid_([0-9a-z]+)_[0-9a-z]+_[0-9a-f]{6}$")


def _base36(number: int) -> str:
    """将非负整数编码为 base36 字符串。"""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number <= 0:
        return "0"

    parts: list[str] = []
    while number:
        number, remainder = divmod(number, 36)
        parts.append(chars[remainder])
    return "".join(reversed(parts))


def new_cid(prefix: str = "cid") -> str:
    """生成新的对话标识。"""
    timestamp = _base36(int(time.time()))
    random_part = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{random_part}"


def new_sid(cid: str, prefix: str = "sid") -> str:
    """生成与对话标识关联的会话标识。"""
    timestamp = _base36(int(time.time() * 1000))
    random_part = uuid.uuid4().hex[:6]
    cid_part = cid.split("_", 2)[1]
    return f"{prefix}_{cid_part}_{timestamp}_{random_part}"


def valid_session_ids(cid: typing.Any, sid: typing.Any) -> bool:
    """校验 cid/sid 格式及其会话关联关系。"""
    cid_text = str(cid or "").strip()
    sid_text = str(sid or "").strip()

    cid_match = CID_RE.fullmatch(cid_text)
    sid_match = SID_RE.fullmatch(sid_text)

    if cid_match is None or sid_match is None:
        return False

    return cid_match.group(1) == sid_match.group(1)


def short_uid(length: int = 8) -> str:
    """生成指定长度的短随机标识。"""
    return (
        base64.b32encode(uuid.uuid4().bytes)
        .decode("ascii")
        .rstrip("=")
        .lower()[:length]
    )


def new_request_id(prefix: str = "request") -> str:
    """生成新的幂等请求标识。"""
    normalized_prefix = str(prefix or "request").strip("_-") or "request"
    request_id = f"{normalized_prefix}_{short_uid(24)}"

    return normalize_request_id(request_id)


def normalize_turn_id(value: str) -> str:
    """校验并返回客户端生成的逻辑轮次标识。"""
    turn_id = str(value or "").strip()
    if not TURN_ID_PATTERN.fullmatch(turn_id):
        raise ValueError(
            "turn_id must be 8-128 ASCII letters, digits, underscores or hyphens"
        )
    return turn_id


def normalize_request_id(value: str) -> str:
    """校验并返回客户端生成的幂等请求标识。"""
    request_id = str(value or "").strip()
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError(
            "request_id must be 8-160 ASCII letters, digits, underscores or hyphens"
        )
    return request_id


def resolve_request_id(
    value: str | None,
    *,
    prefix: str = "request"
) -> str:
    """读取已有幂等请求标识，空值则生成新标识。"""
    request_id = str(value or "").strip()
    if request_id:
        return normalize_request_id(request_id)
    return new_request_id(prefix)


def stable_request_id(prefix: str, *parts: typing.Any) -> str:
    """根据一项逻辑命令的稳定字段派生幂等请求标识。"""
    normalized_prefix = str(prefix or "request").strip("_-") or "request"
    encoded = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:40]
    return normalize_request_id(f"{normalized_prefix}_{digest}")


if __name__ == '__main__':
    pass
