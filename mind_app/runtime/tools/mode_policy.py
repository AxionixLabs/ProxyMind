# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

Rule = dict[str, typing.Any]
ToolFilterMode = typing.Literal["chat", "fast", "xtra"]
ModeToolPolicy = dict[str, tuple[Rule, ...] | None]


MODE_TOOL_POLICIES: dict[ToolFilterMode, ModeToolPolicy] = {
    "chat": {
        "deny": (
            {"hidden": True},
            {"domain": "common", "class": "security"},
            {"domain": "bench", "class": "k6"},
            {"domain": "bench", "class": "nexus"},
            {"domain": "media", "class": "ffmpeg"},
        ),
        "allow": None,
    },
    "fast": {
        "deny": (
            {"hidden": True},
            {"domain": "device"},
            {"domain": "bench", "class": "framix"},
            {"domain": "bench", "class": "memrix"},
            {"domain": "media", "class": "scrcpy"},
        ),
        "allow": None,
    },
    "xtra": {
        "deny": (
            {"hidden": True},
        ),
        "allow": (
            {"external": True},
            {"domain": "coding"},
            {"domain": "client", "class": "view"},
            {"domain": "client", "class": "plan"},
            {"name": "close_agent"},
            {"name": "resume_agent"},
            {"name": "send_input"},
            {"name": "spawn_agent"},
            {"name": "wait_agent"},
        ),
    },
}


def filter_mode_tools(
    mode: ToolFilterMode,
    tools: list[dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    """按工具过滤模式处理目录，并保留内联元数据。"""
    policy = _policy_for_mode(mode)
    output: list[dict[str, typing.Any]] = []

    for item in tools or []:
        if not isinstance(item, dict):
            continue

        name = _tool_name(item)
        if str(item.get("type") or "").strip() != "function" and not name:
            output.append(item)
            continue
        if not name:
            continue

        meta = tool_meta_for_item(item)
        if _matches_any_rule(name, meta, policy["deny"] or ()):
            continue

        allow_rules = policy["allow"]
        if allow_rules is not None and not _matches_any_rule(
            name,
            meta,
            allow_rules,
        ):
            continue

        output.append(tool_with_effective_meta(item, meta))

    return output


def _policy_for_mode(mode: ToolFilterMode) -> ModeToolPolicy:
    """返回指定工具过滤模式的策略。"""
    policy = MODE_TOOL_POLICIES.get(mode)
    if policy is None:
        raise ValueError(f"Invalid tool filter mode: {mode}")
    return policy


def _tool_name(tool: dict[str, typing.Any] | None) -> str:
    """从 MCP 或 function 工具结构中读取名称。"""
    if not isinstance(tool, dict):
        return ""

    direct_name = str(tool.get("name") or "").strip()
    if direct_name:
        return direct_name

    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "").strip()
    return ""


def tool_meta_for_item(
    item: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """返回工具描述中携带的内联元数据。"""
    if not isinstance(item, dict):
        return {}

    inline_meta = item.get("meta")
    return dict(inline_meta) if isinstance(inline_meta, dict) else {}


def tool_with_effective_meta(
    item: dict[str, typing.Any],
    meta: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """返回携带有效元数据的工具描述。"""
    if not meta:
        return item

    copied = dict(item)
    copied["meta"] = dict(meta)
    return copied


def _matches_any_rule(
    name: str,
    meta: dict[str, typing.Any],
    rules: typing.Iterable[Rule],
) -> bool:
    """判断工具是否命中任意规则。"""
    return any(
        _matches_rule(name, meta, rule)
        for rule in rules
        if isinstance(rule, dict)
    )


def _matches_rule(
    name: str,
    meta: dict[str, typing.Any],
    rule: Rule,
) -> bool:
    """判断工具是否命中字段间为 AND 关系的单条规则。"""
    if "name" in rule and name != rule["name"]:
        return False
    if "domain" in rule and meta.get("domain") != rule["domain"]:
        return False
    if "class" in rule and meta.get("class") != rule["class"]:
        return False
    if (
        "hidden" in rule
        and bool(meta.get("hidden", False)) != bool(rule["hidden"])
    ):
        return False
    if (
        "external" in rule
        and bool(meta.get("external", False)) != bool(rule["external"])
    ):
        return False
    return True


if __name__ == '__main__':
    pass
