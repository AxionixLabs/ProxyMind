# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import json
import typing
import hashlib
from dataclasses import dataclass
from pathlib import Path
from mind_nova import const

HookEventName = typing.Literal[
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
]

HookFailurePolicy = typing.Literal[
    "continue",
    "block",
]

HookMatcherSubject = typing.Literal[
    "tool_name",
    "compact_trigger",
    "session_reason",
    "agent_type",
]

HookControlPolicy = typing.Literal[
    "gate",
    "permission",
    "notify",
]

HookHandlerType = typing.Literal["command"]

SessionEndReason = typing.Literal[
    "exit",
    "archive",
    "idle",
    "deleted",
    "error",
]

SESSION_END_REASONS: tuple[SessionEndReason, ...] = (
    "exit",
    "archive",
    "idle",
    "deleted",
    "error",
)

CompactTriggerReason = typing.Literal[
    "manual",
    "auto",
    "overflow",
]

COMPACT_TRIGGER_REASONS: tuple[CompactTriggerReason, ...] = (
    "manual",
    "auto",
    "overflow",
)

CompactTriggerSource = typing.Literal["client", "server"]
COMPACT_TRIGGER_SOURCES: tuple[CompactTriggerSource, ...] = (
    "client",
    "server",
)

CompactResultSource = typing.Literal["server", "fallback"]
COMPACT_RESULT_SOURCES: tuple[CompactResultSource, ...] = (
    "server",
    "fallback",
)

CompactOutcome = typing.Literal[
    "completed",
    "failed",
    "interrupted",
]
COMPACT_OUTCOMES: tuple[CompactOutcome, ...] = (
    "completed",
    "failed",
    "interrupted",
)

HOOK_MATCHER_GROUP_FIELDS = frozenset({
    "matcher",
    "hooks",
})

HOOK_HANDLER_FIELDS = frozenset({
    "type",
    "command",
    "commandWindows",
    "command_windows",
    "statusMessage",
    "timeout",
    "async",
    "additionalContextLimit",
})

DEFAULT_HOOK_TIMEOUT_SEC               = 600
DEFAULT_SESSION_END_TIMEOUT_SEC        = 1
MAX_SESSION_END_TIMEOUT_SEC            = 3
DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT = 2500


class HookConfigError(ValueError):
    """表示 Hook 配置不符合约束。"""


@dataclass(frozen=True, slots=True)
class HookEventConfigSpec:
    """定义生命周期事件的配置约束。"""
    name: HookEventName
    description: str
    default_on_error: HookFailurePolicy
    matcher_subject: HookMatcherSubject | None
    control_policy: HookControlPolicy

    @property
    def allows_block_on_error(self) -> bool:
        """返回事件是否允许失败时阻断主流程。"""
        return self.control_policy != "notify"


HOOK_EVENT_CONFIG_SPECS: dict[HookEventName, HookEventConfigSpec] = {
    "PreToolUse": HookEventConfigSpec(
        name="PreToolUse",
        description="Before a tool executes",
        default_on_error="block",
        matcher_subject="tool_name",
        control_policy="gate",
    ),
    "PermissionRequest": HookEventConfigSpec(
        name="PermissionRequest",
        description="When permission is requested",
        default_on_error="continue",
        matcher_subject="tool_name",
        control_policy="permission",
    ),
    "PostToolUse": HookEventConfigSpec(
        name="PostToolUse",
        description="After a tool executes",
        default_on_error="continue",
        matcher_subject="tool_name",
        control_policy="notify",
    ),
    "PreCompact": HookEventConfigSpec(
        name="PreCompact",
        description="Before context compaction",
        default_on_error="block",
        matcher_subject="compact_trigger",
        control_policy="gate",
    ),
    "PostCompact": HookEventConfigSpec(
        name="PostCompact",
        description="After context compaction",
        default_on_error="continue",
        matcher_subject="compact_trigger",
        control_policy="notify",
    ),
    "SessionStart": HookEventConfigSpec(
        name="SessionStart",
        description="When a new session starts",
        default_on_error="continue",
        matcher_subject="session_reason",
        control_policy="notify",
    ),
    "UserPromptSubmit": HookEventConfigSpec(
        name="UserPromptSubmit",
        description="When the user submits a prompt",
        default_on_error="block",
        matcher_subject=None,
        control_policy="gate",
    ),
    "SubagentStart": HookEventConfigSpec(
        name="SubagentStart",
        description="When a subagent is created",
        default_on_error="continue",
        matcher_subject="agent_type",
        control_policy="notify",
    ),
    "SubagentStop": HookEventConfigSpec(
        name="SubagentStop",
        description="Right before a subagent ends its turn",
        default_on_error="continue",
        matcher_subject="agent_type",
        control_policy="notify",
    ),
    "Stop": HookEventConfigSpec(
        name="Stop",
        description="Right before Codex ends its turn",
        default_on_error="continue",
        matcher_subject=None,
        control_policy="notify",
    ),
    "SessionEnd": HookEventConfigSpec(
        name="SessionEnd",
        description="When a root session ends",
        default_on_error="continue",
        matcher_subject="session_reason",
        control_policy="notify",
    ),
}

HOOK_EVENT_NAMES: tuple[HookEventName, ...] = tuple(HOOK_EVENT_CONFIG_SPECS)


@dataclass(frozen=True, slots=True)
class HookHandlerConfig:
    """描述 Hook 使用的执行处理器。"""
    type: HookHandlerType
    command: str
    command_windows: str | None
    status_message: str | None
    timeout_sec: int
    run_async: bool
    additional_context_limit: int

    def command_for_platform(self, platform: str) -> str:
        """返回当前平台应执行的命令。"""
        if platform == "nt" and self.command_windows:
            return self.command_windows
        return self.command


@dataclass(frozen=True, slots=True)
class HookDefinitionConfig:
    """描述已经解析并带有来源信息的 Hook。"""
    key: str
    event: HookEventName
    handler: HookHandlerConfig
    matcher: str
    on_error: HookFailurePolicy
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
            _normalize_matcher_group(event, index, entry)
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
        for group_index, group in enumerate(table.get(event, [])):
            for hook_index, handler in enumerate(group["hooks"]):
                content_hash = _content_hash({
                    "matcher": group["matcher"],
                    "handler": handler,
                })
                definitions.append(HookDefinitionConfig(
                    key=(
                        f"{source_key}:{event}:{group_index}:{hook_index}"
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
                    matcher=group["matcher"],
                    on_error=(
                        HOOK_EVENT_CONFIG_SPECS[event].default_on_error
                    ),
                    source_scope=source_scope,
                    source_path=path_text,
                    content_hash=content_hash,
                ))
    return tuple(definitions)


def _normalize_matcher_group(
    event: HookEventName,
    index: int,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """校验并规范化单个 Hook 匹配组。"""
    dotted = f"hooks.{event}[{index}]"
    if not isinstance(raw, dict):
        raise HookConfigError(f"{dotted} must be a table")

    unknown = sorted(set(raw).difference(HOOK_MATCHER_GROUP_FIELDS))
    if unknown:
        raise HookConfigError(
            f"unknown hook matcher group key: {dotted}.{unknown[0]}"
        )

    matcher = raw.get("matcher")
    if matcher is None:
        matcher = ""
    if not isinstance(matcher, str):
        raise HookConfigError(f"{dotted}.matcher must be a string")
    matcher = matcher.strip()

    try:
        re.compile(".*" if matcher in {"", "*"} else matcher)
    except re.error as error:
        raise HookConfigError(f"{dotted}.matcher is invalid: {error}") from error

    hooks = raw.get("hooks", [])
    if not isinstance(hooks, list):
        raise HookConfigError(f"{dotted}.hooks must be an array")

    return {
        "matcher": matcher,
        "hooks": [
            _normalize_hook_handler(
                handler,
                dotted=f"{dotted}.hooks[{hook_index}]",
                event=event,
            )
            for hook_index, handler in enumerate(hooks)
        ],
    }


def _normalize_hook_handler(
    raw: typing.Any,
    *,
    dotted: str,
    event: HookEventName
) -> dict[str, typing.Any]:
    """校验并规范化单个 Hook 处理器。"""
    path = dotted
    if not isinstance(raw, dict):
        raise HookConfigError(f"{path} must be a table")

    unknown = sorted(set(raw).difference(HOOK_HANDLER_FIELDS))
    if unknown:
        raise HookConfigError(f"unknown hook handler key: {path}.{unknown[0]}")

    handler_type = raw.get("type")
    if handler_type != "command":
        raise HookConfigError(f"{path}.type must be command")

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise HookConfigError(f"{path}.command must be a non-empty string")

    if "commandWindows" in raw and "command_windows" in raw:
        raise HookConfigError(
            f"{path} cannot contain both commandWindows and command_windows"
        )
    command_windows = raw.get(
        "commandWindows",
        raw.get("command_windows"),
    )
    if command_windows is not None:
        if not isinstance(command_windows, str) or not command_windows.strip():
            raise HookConfigError(
                f"{path}.commandWindows must be a non-empty string"
            )
        command_windows = command_windows.strip()

    status_message = raw.get("statusMessage")
    if status_message is not None:
        if not isinstance(status_message, str):
            raise HookConfigError(f"{path}.statusMessage must be a string")
        status_message = status_message.strip() or None

    default_timeout = (
        DEFAULT_SESSION_END_TIMEOUT_SEC
        if event == "SessionEnd"
        else DEFAULT_HOOK_TIMEOUT_SEC
    )
    raw_timeout = raw.get("timeout", default_timeout)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
        raise HookConfigError(f"{path}.timeout must be an integer")
    timeout = raw_timeout
    if timeout < 0:
        raise HookConfigError(f"{path}.timeout must be at least 0")
    if event == "SessionEnd" and timeout > MAX_SESSION_END_TIMEOUT_SEC:
        raise HookConfigError(
            f"{path}.timeout must be at most {MAX_SESSION_END_TIMEOUT_SEC} "
            "for SessionEnd"
        )

    run_async = raw.get("async", False)
    if not isinstance(run_async, bool):
        raise HookConfigError(f"{path}.async must be a boolean")

    context_limit = raw.get(
        "additionalContextLimit",
        DEFAULT_ADDITIONAL_CONTEXT_TOKEN_LIMIT,
    )
    if isinstance(context_limit, bool) or not isinstance(context_limit, int):
        raise HookConfigError(
            f"{path}.additionalContextLimit must be an integer"
        )
    if context_limit < 0:
        raise HookConfigError(
            f"{path}.additionalContextLimit must be at least 0"
        )

    return {
        "type": "command",
        "command": command.strip(),
        "commandWindows": command_windows,
        "statusMessage": status_message,
        "timeout": timeout,
        "async": run_async,
        "additionalContextLimit": context_limit,
    }


def _content_hash(value: dict[str, typing.Any]) -> str:
    """返回 Hook 内容的稳定摘要。"""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode(const.CHARSET)

    return hashlib.sha256(encoded).hexdigest()


if __name__ == '__main__':
    pass
