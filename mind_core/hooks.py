# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import re
import typing
from dataclasses import dataclass

HookEventName = typing.Literal[
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
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

HookTrustPolicy = typing.Literal[
    "managed",
    "content_hash",
]

HookHandlerType = typing.Literal[
    "command",
    "mcp_tool",
    "prompt",
    "agent"
]

HookStateTable = dict[str, dict[str, bool | str]]

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

CompactTriggerSource = typing.Literal[
    "client",
    "server"
]

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

HOOK_STATE_FIELDS = frozenset({
    "enabled",
    "trusted_hash",
})

HOOK_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class HookConfigError(ValueError):
    """表示 Hook 配置不符合约束。"""


@dataclass(frozen=True, slots=True)
class HookEventConfigSpec:
    """定义生命周期事件的配置约束。"""
    name: HookEventName
    description: str
    matcher_subject: HookMatcherSubject | None
    control_policy: HookControlPolicy
    supports_additional_context: bool


HOOK_EVENT_CONFIG_SPECS: dict[HookEventName, HookEventConfigSpec] = {
    "PreToolUse": HookEventConfigSpec(
        name="PreToolUse",
        description="Before a tool executes",
        matcher_subject="tool_name",
        control_policy="gate",
        supports_additional_context=True,
    ),
    "PermissionRequest": HookEventConfigSpec(
        name="PermissionRequest",
        description="When permission is requested",
        matcher_subject="tool_name",
        control_policy="permission",
        supports_additional_context=False,
    ),
    "PostToolUse": HookEventConfigSpec(
        name="PostToolUse",
        description="After a tool executes",
        matcher_subject="tool_name",
        control_policy="notify",
        supports_additional_context=True,
    ),
    "PreCompact": HookEventConfigSpec(
        name="PreCompact",
        description="Before context compaction",
        matcher_subject="compact_trigger",
        control_policy="gate",
        supports_additional_context=False,
    ),
    "PostCompact": HookEventConfigSpec(
        name="PostCompact",
        description="After context compaction",
        matcher_subject="compact_trigger",
        control_policy="notify",
        supports_additional_context=False,
    ),
    "SessionStart": HookEventConfigSpec(
        name="SessionStart",
        description="When a new session starts",
        matcher_subject="session_reason",
        control_policy="notify",
        supports_additional_context=True,
    ),
    "SessionEnd": HookEventConfigSpec(
        name="SessionEnd",
        description="Right before a session ends",
        matcher_subject="session_reason",
        control_policy="notify",
        supports_additional_context=False,
    ),
    "UserPromptSubmit": HookEventConfigSpec(
        name="UserPromptSubmit",
        description="When the user submits a prompt",
        matcher_subject=None,
        control_policy="gate",
        supports_additional_context=True,
    ),
    "SubagentStart": HookEventConfigSpec(
        name="SubagentStart",
        description="When a subagent is created",
        matcher_subject="agent_type",
        control_policy="notify",
        supports_additional_context=True,
    ),
    "SubagentStop": HookEventConfigSpec(
        name="SubagentStop",
        description="Right before a subagent ends its turn",
        matcher_subject="agent_type",
        control_policy="notify",
        supports_additional_context=False,
    ),
    "Stop": HookEventConfigSpec(
        name="Stop",
        description="Right before Codex ends its turn",
        matcher_subject=None,
        control_policy="notify",
        supports_additional_context=False,
    ),
}

HOOK_EVENT_NAMES: tuple[HookEventName, ...] = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)

if set(HOOK_EVENT_CONFIG_SPECS) != set(HOOK_EVENT_NAMES):
    raise RuntimeError("hook event configuration does not match stable order")


@dataclass(frozen=True, slots=True)
class HookHandlerConfig:
    """描述 Hook 使用的执行处理器。"""
    type: HookHandlerType
    command: str | None
    command_windows: str | None
    status_message: str | None
    mcp_server: str | None
    mcp_tool: str | None
    timeout_sec: int
    run_async: bool
    additional_context_limit: int

    @property
    def selector(self) -> str:
        """返回用于稳定身份区分的处理器选择器。"""
        if self.type == "mcp_tool":
            return f"mcp_tool:{self.mcp_server or ''}/{self.mcp_tool or ''}"
        return self.type

    def command_for_platform(self, platform: str) -> str:
        """返回当前平台应执行的命令。"""
        if self.type != "command" or self.command is None:
            raise HookConfigError(
                f"handler type {self.type!r} does not provide a command"
            )
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
    source_scope: str
    source_path: str | None
    trust_policy: HookTrustPolicy
    content_hash: str


def normalize_hook_state_table(raw: typing.Any) -> HookStateTable:
    """校验并规范化用户配置中的 Hook 状态表。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HookConfigError("hooks.state must be a table")

    normalized: HookStateTable = {}
    for raw_key, raw_state in raw.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise HookConfigError("hooks.state key must be a non-empty string")
        key = raw_key.strip()
        if not isinstance(raw_state, dict):
            raise HookConfigError(f"hooks.state.{key} must be a table")

        unknown = sorted(set(raw_state).difference(HOOK_STATE_FIELDS))
        if unknown:
            raise HookConfigError(
                f"unknown hook state key: hooks.state.{key}.{unknown[0]}"
            )

        state: dict[str, bool | str] = {}
        if "enabled" in raw_state:
            enabled = raw_state["enabled"]
            if not isinstance(enabled, bool):
                raise HookConfigError(
                    f"hooks.state.{key}.enabled must be a boolean"
                )
            state["enabled"] = enabled

        if "trusted_hash" in raw_state:
            trusted_hash = raw_state["trusted_hash"]
            if (
                not isinstance(trusted_hash, str)
                or not HOOK_HASH_PATTERN.fullmatch(trusted_hash)
            ):
                raise HookConfigError(
                    f"hooks.state.{key}.trusted_hash must be a sha256 hash"
                )
            state["trusted_hash"] = trusted_hash

        normalized[key] = state

    return normalized


if __name__ == '__main__':
    pass
