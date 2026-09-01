# -*- coding: utf-8 -*-

import hashlib
import re
import typing

from mcp import types as mcp_types

from metadata import const


def slugify_mcp_name(value: typing.Any, fallback: str = "server") -> str:
    """把服务名称转换为稳定的短横线标识。"""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def truncate_text(value: typing.Any, limit: int) -> str:
    """按指定长度截断 MCP 元数据文本。"""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_tool_component(value: typing.Any, fallback: str) -> str:
    """生成可用于工具名片段的安全字符串。"""
    text = str(value or "").strip()
    safe = "".join(
        character
        if character.isalnum() or character in {"_", "-"}
        else "_"
        for character in text
    )
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or fallback


def _limit_tool_name(value: str) -> str:
    """限制外部工具名长度，超长时附加摘要后缀。"""
    max_external_name_len = 2048
    if len(value) <= max_external_name_len:
        return value

    digest = hashlib.sha1(
        value.encode(const.CHARSET, errors="ignore")
    ).hexdigest()[:8]
    prefix_len = max_external_name_len - len(digest) - 1
    return f"{value[:prefix_len]}_{digest}"


def tool_name_hook(
    name: str,
    server_info: mcp_types.Implementation,
) -> str:
    """生成带服务名前缀的外部工具展示名。"""
    alias = _safe_tool_component(
        slugify_mcp_name(server_info.name, fallback="server"),
        "server",
    )
    tool_name = _safe_tool_component(name, "tool")
    return _limit_tool_name(f"mcp__{alias}__{tool_name}")
