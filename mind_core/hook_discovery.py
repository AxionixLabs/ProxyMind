# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import json
import typing
import hashlib
from dataclasses import dataclass
from pathlib import Path
from mind_core.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HOOK_EVENT_NAMES,
    HookConfigError,
    HookDefinitionConfig,
    HookEventName,
    HookHandlerConfig,
    HookStateTable
)
from mind_nova import const

_MATCHER_GROUP_FIELDS = frozenset({
    "matcher",
    "hooks",
})

_COMMAND_HANDLER_FIELDS = frozenset({
    "type",
    "command",
    "commandWindows",
    "command_windows",
    "statusMessage",
    "timeout",
    "async",
    "additionalContextLimit",
})

_DEFAULT_HOOK_TIMEOUT_SEC               = 600
_DEFAULT_SESSION_END_TIMEOUT_SEC        = 1
_MAX_SESSION_END_TIMEOUT_SEC            = 3
_DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT = 2500


@dataclass(frozen=True, slots=True)
class _NormalizedHandler:
    """保存处理器的原始位置和规范配置。"""
    source_index: int
    config: dict[str, typing.Any]


@dataclass(frozen=True, slots=True)
class _NormalizedMatcherGroup:
    """保存匹配组的原始位置和有效处理器。"""
    source_index: int
    matcher: str
    handlers: tuple[_NormalizedHandler, ...]


def normalize_hook_table(
    raw: typing.Any,
    *,
    warnings: list[str] | None = None,
    source: str = "config"
) -> dict[str, typing.Any]:
    """规范化 Hook 配置并跳过可局部恢复的无效项。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HookConfigError("hooks must be a table")

    normalized: dict[str, typing.Any] = {}
    if "state" in raw:
        normalized["state"] = _discover_hook_state_table(raw.get("state"))

    groups_by_event = _discover_hook_groups(
        raw,
        warnings=warnings,
        source=source,
    )
    for event, groups in groups_by_event.items():
        normalized[event] = [
            {
                "matcher": group.matcher,
                "hooks": [
                    dict(handler.config)
                    for handler in group.handlers
                ],
            }
            for group in groups
        ]

    return normalized


def resolve_hook_definitions(
    raw: typing.Any,
    *,
    source_scope: str,
    source_path: Path | None,
    warnings: list[str] | None = None
) -> tuple[HookDefinitionConfig, ...]:
    """把单个配置层中的 Hook 表转换为来源化定义。"""
    path_text  = str(source_path.resolve()) if source_path is not None else None
    source_key = path_text or source_scope
    source     = path_text or source_scope

    groups_by_event = _discover_hook_groups(
        raw,
        warnings=warnings,
        source=source,
    )

    definitions: list[HookDefinitionConfig] = []

    for event in HOOK_EVENT_NAMES:
        for group in groups_by_event.get(event, ()):
            for normalized_handler in group.handlers:
                handler = normalized_handler.config
                content_hash = _content_hash({
                    "matcher": group.matcher,
                    "handler": handler,
                })
                definitions.append(HookDefinitionConfig(
                    key=(
                        f"{source_key}:{event}:{group.source_index}:"
                        f"{normalized_handler.source_index}"
                    ),
                    event=event,
                    handler=HookHandlerConfig(
                        type=handler["type"],
                        command=handler["command"],
                        command_windows=handler["commandWindows"],
                        status_message=handler["statusMessage"],
                        timeout_sec=handler["timeout"],
                        run_async=handler["async"],
                        additional_context_limit=(
                            handler["additionalContextLimit"]
                        ),
                    ),
                    matcher=group.matcher,
                    source_scope=source_scope,
                    source_path=path_text,
                    content_hash=content_hash,
                ))
    return tuple(definitions)


def _discover_hook_state_table(raw: typing.Any) -> HookStateTable:
    """读取可用 Hook 状态并静默忽略格式错误的条目。"""
    if not isinstance(raw, dict):
        return {}

    normalized: HookStateTable = {}

    for raw_key, raw_state in raw.items():
        if (
            not isinstance(raw_key, str)
            or not raw_key.strip()
            or not isinstance(raw_state, dict)
        ):
            continue

        enabled      = raw_state.get("enabled")
        trusted_hash = raw_state.get("trusted_hash")

        if "enabled" in raw_state and not isinstance(enabled, bool):
            continue
        if "trusted_hash" in raw_state and not isinstance(trusted_hash, str):
            continue

        state: dict[str, bool | str] = {}
        if isinstance(enabled, bool):
            state["enabled"] = enabled
        if isinstance(trusted_hash, str):
            state["trusted_hash"] = trusted_hash
        normalized[raw_key.strip()] = state

    return normalized


def _discover_hook_groups(
    raw: typing.Any,
    *,
    warnings: list[str] | None,
    source: str
) -> dict[HookEventName, tuple[_NormalizedMatcherGroup, ...]]:
    """发现各事件中的有效匹配组。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HookConfigError("hooks must be a table")

    for key in sorted(set(raw).difference((*HOOK_EVENT_NAMES, "state"))):
        _append_warning(
            warnings,
            f"ignoring unsupported hook event {key!r} in {source}",
        )

    discovered: dict[HookEventName, tuple[_NormalizedMatcherGroup, ...]] = {}

    for event in HOOK_EVENT_NAMES:
        entries = raw.get(event)
        if entries is None:
            continue
        if not isinstance(entries, list):
            _append_warning(
                warnings,
                f"skipping {event} hooks in {source}: expected an array of tables",
            )
            continue

        groups = tuple(
            group
            for index, entry in enumerate(entries)
            if (
                group := _normalize_matcher_group(
                    event,
                    index,
                    entry,
                    warnings=warnings,
                    source=source,
                )
            ) is not None
        )
        discovered[event] = groups

    return discovered


def _normalize_matcher_group(
    event: HookEventName,
    index: int,
    raw: typing.Any,
    *,
    warnings: list[str] | None,
    source: str
) -> _NormalizedMatcherGroup | None:
    """规范化单个 Hook 匹配组，失败时跳过该组。"""
    dotted = f"hooks.{event}[{index}]"

    if not isinstance(raw, dict):
        _append_warning(
            warnings,
            f"skipping {dotted} in {source}: expected a table",
        )
        return None

    unknown = sorted(set(raw).difference(_MATCHER_GROUP_FIELDS))
    if unknown:
        _append_warning(
            warnings,
            f"ignoring unknown fields in {dotted} from {source}: "
            f"{', '.join(unknown)}",
        )

    matcher = raw.get("matcher")
    if matcher is None:
        matcher = ""
    if not isinstance(matcher, str):
        _append_warning(
            warnings,
            f"skipping {dotted} in {source}: matcher must be a string",
        )
        return None

    matcher = matcher.strip()

    if HOOK_EVENT_CONFIG_SPECS[event].matcher_subject is None:
        matcher = ""
    else:
        try:
            re.compile(".*" if matcher in {"", "*"} else matcher)
        except re.error as error:
            _append_warning(
                warnings,
                f"invalid matcher {matcher!r} in {dotted} from {source}: {error}",
            )
            return None

    hooks = raw.get("hooks", [])
    if not isinstance(hooks, list):
        _append_warning(
            warnings,
            f"skipping {dotted} in {source}: hooks must be an array",
        )
        return None

    handlers = tuple(
        _NormalizedHandler(hook_index, normalized)
        for hook_index, handler in enumerate(hooks)
        if (
            normalized := _normalize_hook_handler(
                handler,
                dotted=f"{dotted}.hooks[{hook_index}]",
                event=event,
                warnings=warnings,
                source=source,
            )
        ) is not None
    )

    return _NormalizedMatcherGroup(index, matcher, handlers)


def _normalize_hook_handler(
    raw: typing.Any,
    *,
    dotted: str,
    event: HookEventName,
    warnings: list[str] | None,
    source: str
) -> dict[str, typing.Any] | None:
    """规范化单个 Hook 处理器，失败时跳过该项。"""
    if not isinstance(raw, dict):
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: expected a table",
        )
        return None

    handler_type = raw.get("type")
    if handler_type in {"prompt", "agent"}:
        _append_warning(
            warnings,
            f"skipping {handler_type} hook in {source}: "
            f"{handler_type} hooks are not supported yet",
        )
        return None
    if handler_type != "command":
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: unsupported handler type "
            f"{handler_type!r}",
        )
        return None

    unknown = sorted(set(raw).difference(_COMMAND_HANDLER_FIELDS))
    if unknown:
        _append_warning(
            warnings,
            f"ignoring unknown fields in {dotted} from {source}: "
            f"{', '.join(unknown)}",
        )

    command_fields = _normalize_command_fields(
        raw,
        dotted=dotted,
        warnings=warnings,
        source=source,
    )

    if command_fields is None:
        return None

    command, command_windows, status_message = command_fields

    timeout = _normalize_timeout(
        event,
        raw.get("timeout"),
        dotted=dotted,
        warnings=warnings,
        source=source,
    )
    if timeout is None:
        return None

    run_async = _normalize_async(
        event,
        raw.get("async", False),
        dotted=dotted,
        warnings=warnings,
        source=source,
    )
    if run_async is None:
        return None

    context_limit = _normalize_additional_context_limit(
        event,
        raw.get(
            "additionalContextLimit",
            _DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT,
        ),
        explicitly_configured="additionalContextLimit" in raw,
        dotted=dotted,
        warnings=warnings,
        source=source,
    )
    if context_limit is None:
        return None

    return {
        "type": "command",
        "command": command,
        "commandWindows": command_windows,
        "statusMessage": status_message,
        "timeout": timeout,
        "async": run_async,
        "additionalContextLimit": context_limit,
    }


def _normalize_command_fields(
    raw: dict[str, typing.Any],
    *,
    dotted: str,
    warnings: list[str] | None,
    source: str
) -> tuple[str, str | None, str | None] | None:
    """规范化命令及其平台覆盖和状态消息。"""
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        _append_warning(
            warnings,
            f"skipping empty hook command in {source} ({dotted})",
        )
        return None

    if "commandWindows" in raw and "command_windows" in raw:
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: commandWindows and "
            "command_windows cannot both be set",
        )
        return None

    command_windows = raw.get(
        "commandWindows",
        raw.get("command_windows"),
    )

    if command_windows is not None:
        if not isinstance(command_windows, str):
            _append_warning(
                warnings,
                f"skipping hook in {dotted} from {source}: commandWindows "
                "must be a string",
            )
            return None

        command_windows = command_windows.strip()

        if os.name == "nt" and not command_windows:
            _append_warning(
                warnings,
                f"skipping empty hook command in {source} ({dotted})",
            )
            return None
        command_windows = command_windows or None

    status_message = raw.get("statusMessage")
    if status_message is not None:
        if not isinstance(status_message, str):
            _append_warning(
                warnings,
                f"skipping hook in {dotted} from {source}: statusMessage must "
                "be a string",
            )
            return None
        status_message = status_message.strip() or None

    return command.strip(), command_windows, status_message


def _normalize_timeout(
    event: HookEventName,
    raw_timeout: typing.Any,
    *,
    dotted: str,
    warnings: list[str] | None,
    source: str
) -> int | None:
    """规范化命令 Hook 的超时策略。"""
    if raw_timeout is None:
        raw_timeout = (
            _DEFAULT_SESSION_END_TIMEOUT_SEC
            if event == "SessionEnd"
            else _DEFAULT_HOOK_TIMEOUT_SEC
        )
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: timeout must be an integer",
        )
        return None
    if raw_timeout < 0:
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: timeout must be non-negative",
        )
        return None

    timeout = max(1, raw_timeout)
    if event == "SessionEnd" and timeout > _MAX_SESSION_END_TIMEOUT_SEC:
        _append_warning(
            warnings,
            f"clamping SessionEnd hook timeout to {_MAX_SESSION_END_TIMEOUT_SEC}s "
            f"in {source}",
        )
        timeout = _MAX_SESSION_END_TIMEOUT_SEC
    return timeout


def _normalize_async(
    event: HookEventName,
    value: typing.Any,
    *,
    dotted: str,
    warnings: list[str] | None,
    source: str
) -> bool | None:
    """规范化当前支持范围内的异步配置。"""
    if not isinstance(value, bool):
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: async must be a boolean",
        )
        return None
    if value and event != "SessionEnd":
        _append_warning(
            warnings,
            f"skipping async hook in {source}: async hooks are not supported yet",
        )
        return None
    if value:
        _append_warning(
            warnings,
            f"running async SessionEnd hook synchronously in {source}",
        )
    return value


def _normalize_additional_context_limit(
    event: HookEventName,
    value: typing.Any,
    *,
    explicitly_configured: bool,
    dotted: str,
    warnings: list[str] | None,
    source: str,
) -> int | None:
    """规范化模型输入阈值并忽略不支持事件的配置。"""
    if isinstance(value, bool) or not isinstance(value, int):
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: "
            "additionalContextLimit must be an integer",
        )
        return None
    if value < 0:
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: "
            "additionalContextLimit must be non-negative",
        )
        return None

    if (
        not HOOK_EVENT_CONFIG_SPECS[event].supports_additional_context
        and event not in {"Stop", "SubagentStop"}
    ):
        if explicitly_configured:
            _append_warning(
                warnings,
                f"ignoring additionalContextLimit for {event} hook in {source}: "
                "this event cannot emit additionalContext",
            )
        return _DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT

    return value


def _append_warning(warnings: list[str] | None, message: str) -> None:
    """按需追加非重复 Hook discovery warning。"""
    if warnings is not None and message not in warnings:
        warnings.append(message)


def _content_hash(value: dict[str, typing.Any]) -> str:
    """返回 Hook 内容的稳定摘要。"""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode(const.CHARSET)

    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


if __name__ == '__main__':
    pass
