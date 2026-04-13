# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

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


def summarize_args(args: typing.Mapping[str, typing.Any] | None) -> dict[str, typing.Any]:
    """生成参数摘要，用于开始/结束日志。"""
    return sanitize_value(dict(args or {}), max_depth=2)


def summarize_command(cmd: list[str] | str) -> typing.Any:
    """生成命令摘要，用于子进程边界日志。"""
    if isinstance(cmd, str):
        return clip_text(sanitize_value(cmd), limit=220)
    return sanitize_value(list(cmd), max_depth=1, max_items=12)


def summarize_request_target(request: typing.Mapping[str, typing.Any] | None) -> str:
    """提取请求目标摘要，便于在收口日志里快速定位失败对象。"""
    data = dict(request or {})

    url = str(data.get("url") or "").strip()
    if url:
        return clip_text(url, limit=180)

    host = str(data.get("host") or "").strip()
    port = data.get("port")
    path = str(data.get("path") or "").strip()
    action = str(data.get("action") or "").strip()

    parts: list[str] = []
    if host:
        target = host
        if port not in (None, ""):
            target = f"{target}:{port}"
        if path:
            target = f"{target}/{path.lstrip('/')}"
        parts.append(target)
    elif path:
        parts.append(path)

    if action:
        parts.append(f"action={action}")

    if parts:
        return clip_text(" ".join(parts), limit=180)
    return "<unknown>"


def summarize_result_failure(data: typing.Mapping[str, typing.Any] | None) -> str:
    """提取结果包的主要失败原因，用于 step/result 收口日志。"""
    payload        = dict(data or {})
    response       = payload.get("response") or {}
    error          = payload.get("error")
    assert_summary = payload.get("assert_summary") or {}
    extract        = payload.get("extract") or {}

    if error:
        return clip_text(f"error={error}", limit=180)

    assert_fail = int(assert_summary.get("fail", 0) or 0)
    if assert_fail > 0:
        return f"assert_fail={assert_fail}"

    status = response.get("status")
    if status is not None:
        return f"status={status}"

    if extract:
        return f"extract={len(extract)}"

    return "unknown"


if __name__ == '__main__':
    pass
