# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

Rule = dict[str, typing.Any]

ToolFilterMode = typing.Literal["app", "api"]
ModeToolPolicy = dict[str, tuple[Rule, ...] | None]


MODE_TOOL_POLICIES: dict[ToolFilterMode, ModeToolPolicy] = {
    "app": {
        "deny": (
            {"hidden": True},
        ),
        "allow": (
            {"external": True},
            {"domain": "device"},
            {"domain": "bench", "class": "framix"},
            {"domain": "bench", "class": "memrix"},
            {"domain": "media", "class": "audio"},
            {"domain": "media", "class": "scrcpy"},
            {"domain": "common", "class": "runtime"},
            {"domain": "common", "class": "inspect"},
        ),
    },
    "api": {
        "deny": (
            {"hidden": True},
        ),
        "allow": (
            {"external": True},
            {"domain": "bench", "class": "nexus"},
            {"domain": "common", "class": "security"},
            {"domain": "common", "class": "runtime"},
            {"domain": "common", "class": "inspect"},
        ),
    },
}

DEFAULT_TOOL_POLICY: ModeToolPolicy = {
    "deny": (
        {"hidden": True},
    ),
    "allow": None,
}


def merges_tool_start_event(name: str) -> bool:
    """判断工具开始事件是否应与完成事件合并。"""
    return str(name or "").strip() != "js_repl"


def is_approval_only_tool(name: str) -> bool:
    """判断工具是否只通过专用审批表面反馈结果。"""

    return str(name or "").strip() == "request_permissions"


def filter_mode_tools(
    mode: ToolFilterMode | None,
    tools: list[dict[str, typing.Any]]
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
        if not _is_allowed(mode, name, meta, allow_rules):
            continue

        output.append(tool_with_effective_meta(item, meta))

    return output


def tool_meta_for_item(
    item: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """返回工具描述中携带的内联元数据。"""
    if not isinstance(item, dict):
        return {}

    inline_meta = item.get("meta")
    return dict(inline_meta) if isinstance(inline_meta, dict) else {}


def tool_with_effective_meta(
    item: dict[str, typing.Any],
    meta: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """返回携带有效元数据的工具描述。"""
    if not meta:
        return item

    copied = dict(item)
    copied["meta"] = dict(meta)
    return copied


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


def _policy_for_mode(mode: ToolFilterMode | None) -> ModeToolPolicy:
    """返回指定工具过滤模式的策略。"""
    if mode is None:
        return DEFAULT_TOOL_POLICY

    policy = MODE_TOOL_POLICIES.get(mode)
    if policy is None:
        raise ValueError(f"Invalid tool filter mode: {mode}")
    return policy


def _is_allowed(
    mode: ToolFilterMode | None,
    name: str,
    meta: dict[str, typing.Any],
    allow_rules: tuple[Rule, ...] | None
) -> bool:
    """按工具来源和能力规则判断工具是否可见。"""
    if mode is None:
        return not _is_plan_steps(meta)

    if (
        bool(meta.get("client_builtin", False))
        and not bool(meta.get("external", False))
    ):
        return _allows_client_builtin(mode, meta)

    if allow_rules is None:
        return True
    return _matches_any_rule(name, meta, allow_rules)


def _allows_client_builtin(
    mode: ToolFilterMode,
    meta: dict[str, typing.Any]
) -> bool:
    """保留客户端内置工具，并处理需要限制范围的控制能力。"""
    if meta.get("domain") != "client":
        return True

    if _is_plan_steps(meta):
        return mode == "app"
    return True


def _is_plan_steps(meta: dict[str, typing.Any]) -> bool:
    """判断工具是否为仅供应用自动化使用的步骤规划能力。"""
    return (
        bool(meta.get("client_builtin", False))
        and meta.get("domain") == "client"
        and meta.get("class") == "loop"
    )


def _matches_any_rule(
    name: str,
    meta: dict[str, typing.Any],
    rules: typing.Iterable[Rule]
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
    rule: Rule
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
