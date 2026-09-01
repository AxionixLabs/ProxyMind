# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SENSITIVE_KEYS = {
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "headers",
    "password",
    "secret",
    "session_id",
    "set-cookie",
    "token",
}


def clip_text(value: typing.Any, limit: int = 160) -> str:
    """把任意值裁剪成适合日志展示的短文本。"""
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<{len(text)}>"


def mask_secret(value: typing.Any) -> str:
    """对敏感值做统一脱敏。"""
    text = str(value or "")
    if not text:
        return "***"
    if len(text) <= 8:
        return "***"
    return f"{text[:2]}***{text[-4:]}"


def is_sensitive_key(key: typing.Any) -> bool:
    """判断字段名是否属于敏感信息。"""
    text = str(key or "").strip().lower()
    return any(item in text for item in SENSITIVE_KEYS)


def sanitize_value(
    value: typing.Any,
    *,
    key: typing.Any = None,
    depth: int = 0,
    max_depth: int = 2,
    max_items: int = 8
) -> typing.Any:
    """递归裁剪并脱敏日志中的对象。"""
    if is_sensitive_key(key):
        return mask_secret(value)

    if depth >= max_depth:
        if isinstance(value, (list, tuple, set)):
            return f"<{type(value).__name__}:{len(value)}>"
        if isinstance(value, dict):
            return f"<dict:{len(value)}>"
        if isinstance(value, (bytes, bytearray)):
            return f"<bytes:{len(value)}>"
        return clip_text(value)

    if isinstance(value, dict):
        items = list(value.items())
        out = {
            str(k): sanitize_value(v, key=k, depth=depth + 1, max_depth=max_depth, max_items=max_items)
            for k, v in items[:max_items]
        }
        if len(items) > max_items:
            out["_truncated_items"] = len(items) - max_items
        return out

    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        out = [
            sanitize_value(item, key=key, depth=depth + 1, max_depth=max_depth, max_items=max_items)
            for item in seq[:max_items]
        ]
        if len(seq) > max_items:
            out.append(f"...<{len(seq) - max_items} more>")
        return out

    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"

    if isinstance(value, str):
        return clip_text(value)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return clip_text(value)


if __name__ == '__main__':
    pass
