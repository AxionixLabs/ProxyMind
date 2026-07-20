# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import uuid
import base64
from mind_nova import const


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


def short_uid(length: int = 8) -> str:
    """生成指定长度的短随机标识。"""
    return (
        base64.b32encode(uuid.uuid4().bytes)
        .decode(const.CHARSET)
        .rstrip("=")
        .lower()[:length]
    )


if __name__ == '__main__':
    pass
