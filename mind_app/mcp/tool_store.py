# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def tool_name(tool: typing.Any) -> str:
    """返回传输层工具描述中的工具名。"""
    if not isinstance(tool, dict):
        return ""
    return str(tool.get("name") or "").strip()


def item_meta(tool: typing.Any) -> dict[str, typing.Any]:
    """返回传输层工具描述中的 meta。"""
    if not isinstance(tool, dict):
        return {}
    meta = tool.get("meta")
    return dict(meta) if isinstance(meta, dict) else {}


def find_tool(
    tools: list[dict[str, typing.Any]],
    name: str
) -> dict[str, typing.Any] | None:
    """按 name 从传输层工具列表中查找工具。"""
    target = str(name or "").strip()
    if not target:
        return None

    for tool in tools:
        if tool_name(tool) == target:
            return tool
    return None


def meta_for_tool(
    tools: list[dict[str, typing.Any]],
    name: str
) -> dict[str, typing.Any]:
    """按 name 从传输层工具列表中读取 meta。"""
    tool = find_tool(tools, name)
    return item_meta(tool)


def has_tool(
    tools: list[dict[str, typing.Any]],
    name: str
) -> bool:
    """判断传输层工具列表中是否存在指定工具。"""
    return find_tool(tools, name) is not None


if __name__ == '__main__':
    pass
