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
    """把任意值裁剪成短文本。"""
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<{len(text)}>"


def mask_secret(value: typing.Any) -> str:
    """对敏感值做脱敏处理。"""
    text = str(value or "")
    if not text or len(text) <= 8:
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
    """递归裁剪并脱敏日志对象。"""
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
        result = {
            str(item_key): sanitize_value(
                item_value,
                key=item_key,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items
            )
            for item_key, item_value in items[:max_items]
        }
        if len(items) > max_items:
            result["_truncated_items"] = len(items) - max_items
        return result

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            sanitize_value(item, key=key, depth=depth + 1, max_depth=max_depth, max_items=max_items)
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            result.append(f"...<{len(items) - max_items} more>")
        return result

    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str):
        return clip_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return clip_text(value)


def summarize_command(cmd: list[str] | str) -> typing.Any:
    """生成命令摘要。"""
    if isinstance(cmd, str):
        return clip_text(sanitize_value(cmd), limit=220)
    return sanitize_value(list(cmd), max_depth=1, max_items=12)


if __name__ == '__main__':
    pass

