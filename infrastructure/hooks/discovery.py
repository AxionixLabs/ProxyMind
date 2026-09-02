# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import os
import re
import typing
from dataclasses import dataclass
from pathlib import Path

from agent.domain.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HOOK_EVENT_NAMES,
    _DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT,
    CommandHookHandlerConfig,
    HookConfigError,
    HookDefinitionConfig,
    HookEventName,
    McpToolHookHandlerConfig,
    HookStateTable,
    HookTrustPolicy
)
from metadata import const

_MATCHER_GROUP_FIELDS = frozenset({
    "matcher",
    "hooks",
})

_HANDLER_FIELDS = frozenset({
    "type",
    "command",
    "commandWindows",
    "command_windows",
    "statusMessage",
    "timeout",
    "async",
    "additionalContextLimit",
    "server",
    "tool",
    "input",
})

_DEFAULT_HOOK_TIMEOUT_SEC = 600
_DEFAULT_SESSION_END_TIMEOUT_SEC = 1
_MAX_SESSION_END_TIMEOUT_SEC = 3

HOOKS_FILE_NAME = "hooks.json"

_HOOK_FILE_FIELDS = frozenset({
    "description",
    "hooks",
})


@dataclass(frozen=True, slots=True)
class _NormalizedHandler:
    """保存处理器的原始位置和规范配置。"""
    source_index: int
    config: dict[str, typing.Any]
    additional_context_limit: int | None


@dataclass(frozen=True, slots=True)
class _NormalizedMatcherGroup:
    """保存匹配组的原始位置和有效处理器。"""
    source_index: int
    matcher: str
    handlers: tuple[_NormalizedHandler, ...]


@dataclass(frozen=True, slots=True)
class HookSourceResolution:
    """保存单个 Hook 来源的发现结果。"""
    definitions: tuple[HookDefinitionConfig, ...] = ()
    has_events: bool = False


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
    trust_policy: HookTrustPolicy = "content_hash",
    warnings: list[str] | None = None
) -> tuple[HookDefinitionConfig, ...]:
    """把单个配置层中的 Hook 表转换为来源化定义。"""
    return resolve_hook_source(
        raw,
        source_scope=source_scope,
        source_path=source_path,
        trust_policy=trust_policy,
        warnings=warnings,
    ).definitions


def resolve_hook_source(
    raw: typing.Any,
    *,
    source_scope: str,
    source_path: Path | None,
    trust_policy: HookTrustPolicy = "content_hash",
    warnings: list[str] | None = None
) -> HookSourceResolution:
    """解析单个 Hook 来源并保留事件存在信息。"""
    path_text = str(source_path.resolve()) if source_path is not None else None
    source_key = path_text or source_scope
    source = path_text or source_scope

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

                handler_config = _handler_config(
                    handler,
                    additional_context_limit=(
                        normalized_handler.additional_context_limit
                    ),
                    dotted=(
                        f"hooks.{event}[{group.source_index}].hooks["
                        f"{normalized_handler.source_index}]"
                    ),
                )
                if handler_config is None:
                    continue

                definitions.append(HookDefinitionConfig(
                    key=(
                        f"{source_key}|{handler['type']}"
                        + (
                            f":{handler.get('server')}:{handler.get('tool')}"
                            if handler["type"] == "mcp_tool" else ""
                        )
                        + f":{event}:{group.source_index}:"
                          f"{normalized_handler.source_index}"
                    ),
                    event=event,
                    handler=handler_config,
                    matcher=group.matcher,
                    source_scope=source_scope,
                    source_path=path_text,
                    trust_policy=trust_policy,
                    content_hash=content_hash,
                ))

    return HookSourceResolution(
        definitions=tuple(definitions),
        has_events=_has_hook_events(raw),
    )


def resolve_hook_file_source(
    path: Path,
    *,
    source_scope: str,
    trust_policy: HookTrustPolicy = "content_hash",
    warnings: list[str] | None = None
) -> HookSourceResolution:
    """读取独立 Hook 配置文件并返回发现结果。"""
    source_path = Path(path).expanduser()
    if not source_path.is_file():
        return HookSourceResolution()

    try:
        contents = source_path.read_text(encoding=const.CHARSET)
    except (OSError, UnicodeError) as error:
        _append_warning(
            warnings,
            f"failed to read hooks config {source_path}: {error}",
        )
        return HookSourceResolution()

    try:
        document = json.loads(
            contents,
            parse_constant=_reject_json_constant,
        )
        hooks = _hook_file_events(document)
    except (TypeError, ValueError) as error:
        _append_warning(
            warnings,
            f"failed to parse hooks config {source_path}: {error}",
        )
        return HookSourceResolution()

    return resolve_hook_source(
        hooks,
        source_scope=source_scope,
        source_path=source_path,
        trust_policy=trust_policy,
        warnings=warnings,
    )


def _hook_file_events(document: typing.Any) -> dict[str, typing.Any]:
    """原子校验独立 Hook 文件并返回事件表。"""
    if not isinstance(document, dict):
        raise HookConfigError("expected an object")

    unknown = sorted(set(document).difference(_HOOK_FILE_FIELDS))
    if unknown:
        raise HookConfigError(f"unknown field {unknown[0]!r}")

    description = document.get("description")
    if description is not None and not isinstance(description, str):
        raise HookConfigError("description must be a string")

    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        raise HookConfigError("hooks must be an object")

    _validate_hook_file_events(hooks)

    return hooks


def _validate_hook_file_events(hooks: dict[str, typing.Any]) -> None:
    """校验独立 Hook 文件中受支持事件的嵌套结构。"""
    for event in HOOK_EVENT_NAMES:
        if event not in hooks:
            continue

        groups = hooks[event]
        dotted = f"hooks.{event}"
        if not isinstance(groups, list):
            raise HookConfigError(f"{dotted} must be an array")

        for group_index, group in enumerate(groups):
            group_dotted = f"{dotted}[{group_index}]"
            if not isinstance(group, dict):
                raise HookConfigError(f"{group_dotted} must be an object")

            matcher = group.get("matcher")
            if matcher is not None and not isinstance(matcher, str):
                raise HookConfigError(
                    f"{group_dotted}.matcher must be a string"
                )

            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise HookConfigError(
                    f"{group_dotted}.hooks must be an array"
                )

            for handler_index, handler in enumerate(handlers):
                _validate_hook_file_handler(
                    handler,
                    dotted=f"{group_dotted}.hooks[{handler_index}]",
                )


def _validate_hook_file_handler(
    handler: typing.Any,
    *,
    dotted: str
) -> None:
    """校验独立 Hook 文件中的单个处理器结构。"""
    if not isinstance(handler, dict):
        raise HookConfigError(f"{dotted} must be an object")

    handler_type = handler.get("type")
    if handler_type not in ("command", "mcp_tool", "prompt", "agent"):
        raise HookConfigError(
            f"{dotted}.type must be command, mcp_tool, prompt, or agent"
        )

    if handler_type == "mcp_tool":
        for field in ("server", "tool"):
            value = handler.get(field)
            if not isinstance(value, str) or not value.strip():
                raise HookConfigError(f"{dotted}.{field} must be a non-empty string")

    elif handler_type == "command":
        command = handler.get("command")
        if not isinstance(command, str):
            raise HookConfigError(f"{dotted}.command must be a string")

        if "commandWindows" in handler and "command_windows" in handler:
            raise HookConfigError(
                f"{dotted}.commandWindows and command_windows cannot both be set"
            )

        command_windows = handler.get(
            "commandWindows",
            handler.get("command_windows"),
        )

        if command_windows is not None and not isinstance(command_windows, str):
            raise HookConfigError(
                f"{dotted}.commandWindows must be a string"
            )

    timeout = handler.get("timeout")
    if timeout is not None and not _is_non_negative_integer(timeout):
        raise HookConfigError(
            f"{dotted}.timeout must be a non-negative integer"
        )

    if "async" in handler and not isinstance(handler["async"], bool):
        raise HookConfigError(f"{dotted}.async must be a boolean")

    status_message = handler.get("statusMessage")
    if status_message is not None and not isinstance(status_message, str):
        raise HookConfigError(
            f"{dotted}.statusMessage must be a string"
        )

    context_limit = handler.get("additionalContextLimit")
    if (
        context_limit is not None
        and not _is_non_negative_integer(context_limit)
    ):
        raise HookConfigError(
            f"{dotted}.additionalContextLimit must be a non-negative integer"
        )


def _is_non_negative_integer(value: typing.Any) -> bool:
    """判断值是否为非负整数且不是布尔值。"""
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= 0
    )


def _reject_json_constant(value: str) -> typing.NoReturn:
    """拒绝 JSON 标准之外的数值常量。"""
    raise ValueError(f"invalid JSON value {value}")


def _has_hook_events(raw: typing.Any) -> bool:
    """判断已解析来源是否包含非空的受支持事件。"""
    if not isinstance(raw, dict):
        return False
    return any(
        isinstance(raw.get(event), list) and bool(raw[event])
        for event in HOOK_EVENT_NAMES
    )


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

        enabled = raw_state.get("enabled")
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

    normalized_handlers: list[_NormalizedHandler] = []

    for hook_index, handler in enumerate(hooks):
        normalized = _normalize_hook_handler(
            handler,
            dotted=f"{dotted}.hooks[{hook_index}]",
            event=event,
            warnings=warnings,
            source=source,
        )

        if normalized is None:
            continue
        normalized_handlers.append(_NormalizedHandler(
            source_index=hook_index,
            config=normalized,
            additional_context_limit=(
                handler.get("additionalContextLimit")
                if isinstance(handler, dict)
                else None
            ),
        ))
    handlers = tuple(normalized_handlers)

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
    if handler_type not in {"command", "mcp_tool", "prompt", "agent"}:
        _append_warning(
            warnings,
            f"skipping hook in {dotted} from {source}: unsupported handler type "
            f"{handler_type!r}",
        )
        return None

    if handler_type in {"prompt", "agent"}:
        _append_warning(
            warnings,
            f"skipping unsupported {handler_type} hook in {dotted} from {source}",
        )
        return None

    unknown = sorted(set(raw).difference(_HANDLER_FIELDS))
    if unknown:
        _append_warning(
            warnings,
            f"ignoring unknown fields in {dotted} from {source}: "
            f"{', '.join(unknown)}",
        )

    if handler_type == "command":
        command_fields = _normalize_command_fields(
            raw,
            dotted=dotted,
            warnings=warnings,
            source=source,
        )
        if command_fields is None:
            return None
        command, command_windows, status_message = command_fields
    else:
        command = command_windows = None
        status_message = raw.get("statusMessage")
        if status_message is not None:
            if not isinstance(status_message, str):
                _append_warning(
                    warnings,
                    f"skipping hook in {dotted} from {source}: statusMessage must be a string",
                )
                return None
            status_message = status_message.strip() or None

    mcp_server = mcp_tool = None
    mcp_input: dict[str, typing.Any] = {}

    if handler_type == "mcp_tool":
        mcp_server = raw.get("server")
        mcp_tool = raw.get("tool")

        if (
            not isinstance(mcp_server, str) or not mcp_server.strip()
            or not isinstance(mcp_tool, str) or not mcp_tool.strip()
        ):
            _append_warning(
                warnings,
                f"skipping mcp_tool hook in {dotted} from {source}: server and tool must be non-empty strings",
            )
            return None
        mcp_server = mcp_server.strip()
        mcp_tool = mcp_tool.strip()
        mcp_input = _normalize_mcp_input(
            raw.get("input", {}),
            dotted=dotted,
            warnings=warnings,
            source=source,
        )
        if mcp_input is None:
            return None

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
        raw.get("async", False) if handler_type == "command" else False,
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
        "type": handler_type,
        "command": command,
        "commandWindows": command_windows,
        "statusMessage": status_message,
        "server": mcp_server,
        "tool": mcp_tool,
        "input": mcp_input,
        "timeout": timeout,
        "async": run_async,
        "additionalContextLimit": context_limit,
    }


def _normalize_mcp_input(
    value: typing.Any,
    *,
    dotted: str,
    warnings: list[str] | None,
    source: str,
) -> dict[str, typing.Any] | None:
    """校验 MCP Hook 静态输入并固定为可稳定序列化对象。"""
    if not isinstance(value, dict):
        _append_warning(
            warnings,
            f"skipping mcp_tool hook in {dotted} from {source}: "
            "input must be an object",
        )
        return None

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        normalized = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        _append_warning(
            warnings,
            f"skipping mcp_tool hook in {dotted} from {source}: "
            f"input is not stable JSON ({error})",
        )
        return None

    if not isinstance(normalized, dict):
        _append_warning(
            warnings,
            f"skipping mcp_tool hook in {dotted} from {source}: "
            "input must be an object",
        )
        return None
    return normalized


def _handler_config(
    handler: dict[str, typing.Any],
    *,
    additional_context_limit: int | None,
    dotted: str,
) -> CommandHookHandlerConfig | McpToolHookHandlerConfig | None:
    """把边界规范字典转换为可执行的判别联合。"""
    handler_type = handler["type"]
    if handler_type == "command":
        command = handler.get("command")
        if not isinstance(command, str):
            raise HookConfigError(f"{dotted}.command must be a string")
        command_windows = handler.get("commandWindows")
        if command_windows is not None and not isinstance(command_windows, str):
            raise HookConfigError(
                f"{dotted}.commandWindows must be a string"
            )
        status_message = handler.get("statusMessage")
        if status_message is not None and not isinstance(status_message, str):
            raise HookConfigError(
                f"{dotted}.statusMessage must be a string"
            )
        timeout = handler["timeout"]
        run_async = handler["async"]
        context_limit = additional_context_limit
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not isinstance(run_async, bool)
            or (
                context_limit is not None
                and (
                    not isinstance(context_limit, int)
                    or isinstance(context_limit, bool)
                )
            )
        ):
            raise HookConfigError(f"{dotted} contains invalid command fields")
        return CommandHookHandlerConfig(
            type="command",
            command=command,
            command_windows=command_windows,
            status_message=status_message,
            timeout_sec=timeout,
            run_async=run_async,
            additional_context_limit=context_limit,
        )

    if handler_type == "mcp_tool":
        server = handler.get("server")
        tool = handler.get("tool")
        status_message = handler.get("statusMessage")
        timeout = handler["timeout"]
        if (
            not isinstance(server, str)
            or not server
            or not isinstance(tool, str)
            or not tool
            or (status_message is not None and not isinstance(status_message, str))
            or not isinstance(timeout, int)
            or isinstance(timeout, bool)
        ):
            raise HookConfigError(f"{dotted} contains invalid MCP fields")
        return McpToolHookHandlerConfig(
            type="mcp_tool",
            server=server,
            tool=tool,
            input=dict(handler.get("input") or {}),
            status_message=status_message,
            timeout_sec=timeout,
            additional_context_limit=additional_context_limit,
        )

    raise HookConfigError(f"{dotted}.type is not executable")


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
    if value and event == "SessionEnd":
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
