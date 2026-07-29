# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import json
import typing
import hashlib
from dataclasses import dataclass
from pathlib import Path

HookEventName = typing.Literal[
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
]

HookFailurePolicy = typing.Literal[
    "continue",
    "block",
]

HOOK_FIELDS = frozenset({
    "command",
    "matcher",
    "timeout",
    "on_error",
    "enabled",
})

MAX_HOOK_TIMEOUT_SEC = 300.0


class HookConfigError(ValueError):
    """表示 Hook 配置不符合约束。"""


@dataclass(frozen=True, slots=True)
class HookEventConfigSpec:
    """定义生命周期事件的配置约束。"""
    name: HookEventName
    description: str
    default_on_error: HookFailurePolicy
    allows_block_on_error: bool


HOOK_EVENT_CONFIG_SPECS: dict[HookEventName, HookEventConfigSpec] = {
    "PreToolUse": HookEventConfigSpec(
        name="PreToolUse",
        description="Before a tool executes",
        default_on_error="block",
        allows_block_on_error=True,
    ),
    "PermissionRequest": HookEventConfigSpec(
        name="PermissionRequest",
        description="When permission is requested",
        default_on_error="continue",
        allows_block_on_error=True,
    ),
    "PostToolUse": HookEventConfigSpec(
        name="PostToolUse",
        description="After a tool executes",
        default_on_error="continue",
        allows_block_on_error=False,
    ),
}

HOOK_EVENT_NAMES: tuple[HookEventName, ...] = tuple(HOOK_EVENT_CONFIG_SPECS)


@dataclass(frozen=True, slots=True)
class HookDefinitionConfig:
    """描述已经解析并带有来源信息的命令 Hook。"""
    key: str
    event: HookEventName
    command: str
    matcher: str
    timeout_sec: float
    on_error: HookFailurePolicy
    enabled: bool
    source_scope: str
    source_path: str | None
    content_hash: str


def normalize_hook_table(raw: typing.Any) -> dict[str, list[dict[str, typing.Any]]]:
    """校验并规范化 Hook 配置表。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HookConfigError("hooks must be a table")

    unknown_events = sorted(set(raw).difference(HOOK_EVENT_NAMES))
    if unknown_events:
        raise HookConfigError(f"unsupported hook event: {unknown_events[0]}")

    normalized: dict[str, list[dict[str, typing.Any]]] = {}

    for event in HOOK_EVENT_NAMES:
        entries = raw.get(event)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise HookConfigError(f"hooks.{event} must be an array of tables")

        normalized[event] = [
            _normalize_hook_entry(event, index, entry)
            for index, entry in enumerate(entries)
        ]

    return normalized


def resolve_hook_definitions(
    raw: typing.Any,
    *,
    source_scope: str,
    source_path: Path | None
) -> tuple[HookDefinitionConfig, ...]:
    """把单个配置层中的 Hook 表转换为来源化定义。"""
    table      = normalize_hook_table(raw)
    path_text  = str(source_path.resolve()) if source_path is not None else None
    source_key = path_text or source_scope

    definitions: list[HookDefinitionConfig] = []
    for event in HOOK_EVENT_NAMES:
        for index, entry in enumerate(table.get(event, [])):
            content_hash = _content_hash(entry)
            definitions.append(HookDefinitionConfig(
                key=f"{source_key}:{event}:{index}",
                event=event,
                command=entry["command"],
                matcher=entry["matcher"],
                timeout_sec=entry["timeout"],
                on_error=entry["on_error"],
                enabled=entry["enabled"],
                source_scope=source_scope,
                source_path=path_text,
                content_hash=content_hash,
            ))
    return tuple(definitions)


def _normalize_hook_entry(
    event: HookEventName,
    index: int,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """校验并规范化单条 Hook 配置。"""
    dotted = f"hooks.{event}[{index}]"
    if not isinstance(raw, dict):
        raise HookConfigError(f"{dotted} must be a table")

    unknown = sorted(set(raw).difference(HOOK_FIELDS))
    if unknown:
        raise HookConfigError(f"unknown hook key: {dotted}.{unknown[0]}")

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise HookConfigError(f"{dotted}.command must be a non-empty string")

    matcher = raw.get("matcher", "")
    if not isinstance(matcher, str):
        raise HookConfigError(f"{dotted}.matcher must be a string")
    try:
        re.compile(matcher or ".*")
    except re.error as error:
        raise HookConfigError(f"{dotted}.matcher is invalid: {error}") from error

    raw_timeout = raw.get("timeout", 5.0)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        raise HookConfigError(f"{dotted}.timeout must be a number")
    timeout = float(raw_timeout)
    if timeout <= 0.0 or timeout > MAX_HOOK_TIMEOUT_SEC:
        raise HookConfigError(
            f"{dotted}.timeout must be greater than 0 and at most {MAX_HOOK_TIMEOUT_SEC:g}"
        )

    event_spec = HOOK_EVENT_CONFIG_SPECS[event]

    on_error = raw.get("on_error", event_spec.default_on_error)
    if on_error not in {"continue", "block"}:
        raise HookConfigError(f"{dotted}.on_error must be continue or block")

    if on_error == "block" and not event_spec.allows_block_on_error:
        raise HookConfigError(
            f"{dotted}.on_error must be continue for this event"
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise HookConfigError(f"{dotted}.enabled must be a boolean")

    return {
        "command"  : command.strip(),
        "matcher"  : matcher.strip(),
        "timeout"  : timeout,
        "on_error" : on_error,
        "enabled"  : enabled
    }


def _content_hash(value: dict[str, typing.Any]) -> str:
    """返回 Hook 内容的稳定摘要。"""
    executable = {
        key: item
        for key, item in value.items()
        if key != "enabled"
    }
    encoded = json.dumps(
        executable,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


if __name__ == '__main__':
    pass
