# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from dataclasses import dataclass
from mind_core.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HookEventName
)

_REGEX_META = frozenset("\\.^$*+?{}[]()")

TOOL_MATCH_ALIASES: dict[str, tuple[str, ...]] = {
    "shell_command": ("Bash",),
    "exec_command": ("Bash",),
    "write_stdin": ("Bash",),
    "apply_patch": ("Write", "Edit"),
    "spawn_agent": ("Agent",),
}


@dataclass(frozen=True, slots=True)
class HookMatcher:
    """保存一个已经解析的 Hook 匹配规则。"""
    match_all: bool = False
    exact_values: frozenset[str] = frozenset()
    pattern: re.Pattern[str] | None = None

    def matches(self, values: typing.Iterable[str]) -> bool:
        """判断候选值是否命中当前规则。"""
        candidates = tuple(values)
        if self.match_all:
            return True
        if self.pattern is not None:
            return any(self.pattern.search(value) for value in candidates)
        return any(value in self.exact_values for value in candidates)


def compile_hook_matcher(
    event: HookEventName,
    matcher: str
) -> HookMatcher:
    """按生命周期事件语义编译 Hook 匹配规则。"""
    spec       = HOOK_EVENT_CONFIG_SPECS[event]
    normalized = str(matcher or "").strip()

    if spec.matcher_subject is None or normalized in {"", "*"}:
        return HookMatcher(match_all=True)

    alternatives = tuple(normalized.split("|"))

    if all(_is_literal(value) for value in alternatives):
        return HookMatcher(exact_values=frozenset(alternatives))

    return HookMatcher(pattern=re.compile(normalized))


def hook_match_candidates(
    event: HookEventName,
    value: str
) -> tuple[str, ...]:
    """返回生命周期事件需要参与匹配的规范名称和别名。"""
    normalized = str(value or "").strip()
    if HOOK_EVENT_CONFIG_SPECS[event].matcher_subject != "tool_name":
        return (normalized,)

    canonical = _canonical_tool_name(normalized)
    aliases   = TOOL_MATCH_ALIASES.get(canonical, ())

    return tuple(dict.fromkeys((canonical, *aliases, normalized)))


def hook_tool_name(value: str) -> str:
    """返回命令 Hook stdin 使用的 canonical 工具名称。"""
    normalized = str(value or "").strip()
    if normalized in {"shell_command", "exec_command", "write_stdin"}:
        return "Bash"
    return normalized


def _canonical_tool_name(value: str) -> str:
    """把工具兼容别名解析为稳定的本地名称。"""
    for canonical, aliases in TOOL_MATCH_ALIASES.items():
        if value == canonical or value in aliases:
            return canonical
    return value


def _is_literal(value: str) -> bool:
    """判断候选项是否不包含正则控制字符。"""
    return not any(character in _REGEX_META for character in value)


if __name__ == '__main__':
    pass
