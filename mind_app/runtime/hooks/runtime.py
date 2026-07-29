# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
import asyncio
from dataclasses import dataclass
from engine.observability import observe_exception
from mind_core.hooks import HookDefinitionConfig
from mind_app.runtime.execution import ToolInvocation
from .command import HookCommandExecutor
from .models import (
    HookDecision,
    ToolOutcome
)


class HookCommandRunner(typing.Protocol):
    """定义 HookRuntime 使用的命令执行接口。"""

    async def execute(
        self,
        definition: HookDefinitionConfig,
        payload: dict[str, typing.Any],
    ) -> typing.Any:
        """执行命令并返回带 data 字段的结果。"""
        ...


@dataclass(frozen=True, slots=True)
class _RegisteredHook:
    """保存已编译 matcher 的活动 Hook。"""
    definition: HookDefinitionConfig
    matcher: re.Pattern[str]


class HookRuntime:
    """匹配、执行并聚合当前进程启用的生命周期 Hook。"""

    def __init__(
        self,
        definitions: typing.Iterable[HookDefinitionConfig] = (),
        *,
        command_runner: HookCommandRunner | None = None
    ) -> None:
        self.definitions    = tuple(definitions)
        self.command_runner = command_runner or HookCommandExecutor()

        self._active = tuple(
            _RegisteredHook(
                definition=definition,
                matcher=re.compile(definition.matcher or ".*"),
            )
            for definition in self.definitions
            if definition.enabled
        )

    @classmethod
    def empty(cls) -> "HookRuntime":
        """返回不包含活动 Hook 的运行时。"""
        return cls()

    @property
    def installed_count(self) -> int:
        """返回已解析 Hook 数量。"""
        return len(self.definitions)

    @property
    def active_count(self) -> int:
        """返回已启用 Hook 数量。"""
        return len(self._active)

    async def pre_tool_use(self, invocation: ToolInvocation) -> HookDecision:
        """执行匹配的 PreToolUse Hook 并聚合阻止决定。"""
        reasons: list[str]     = []
        denied_keys: list[str] = []

        for registered in self._matching("PreToolUse", invocation.name):
            definition = registered.definition

            try:
                output = await self.command_runner.execute(
                    definition,
                    self._tool_payload("PreToolUse", invocation),
                )
                data = (
                    output.data
                    if isinstance(getattr(output, "data", None), dict)
                    else {}
                )

                denied, reason = _pre_output_decision(data)

            except asyncio.CancelledError:
                raise

            except Exception as error:
                self._observe_failure(definition, invocation, error)
                if definition.on_error == "block":
                    denied_keys.append(definition.key)
                    reasons.append(_bounded_reason(f"hook failed: {error}"))
                continue

            if denied:
                denied_keys.append(definition.key)
                reasons.append(_bounded_reason(reason))

        if denied_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(denied_keys),
            )

        return HookDecision.allow()

    async def post_tool_use(
        self,
        invocation: ToolInvocation,
        outcome: ToolOutcome
    ) -> None:
        """执行匹配的 PostToolUse Hook。"""
        matching = self._matching("PostToolUse", invocation.name)
        if not matching:
            return None

        payload = self._tool_payload(
            "PostToolUse",
            invocation,
            outcome=outcome,
        )
        for registered in matching:
            definition = registered.definition
            try:
                await self.command_runner.execute(definition, payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._observe_failure(definition, invocation, error)

    def _matching(
        self,
        event: str,
        tool_name: str
    ) -> tuple[_RegisteredHook, ...]:
        """返回事件和工具名都匹配的活动 Hook。"""
        return tuple(
            registered
            for registered in self._active
            if registered.definition.event == event
            and registered.matcher.search(tool_name)
        )

    @staticmethod
    def _tool_payload(
        event: str,
        invocation: ToolInvocation,
        *,
        outcome: ToolOutcome | None = None
    ) -> dict[str, typing.Any]:
        """构建不包含内部执行授权的工具 Hook 输入。"""
        turn = invocation.turn

        payload: dict[str, typing.Any] = {
            "session_id": turn.agent.root_session_id,
            "turn_id": turn.turn_id,
            "cwd": turn.cwd,
            "hook_event_name": event,
            "model": turn.model,
            "sandbox_mode": turn.permissions.sandbox_mode,
            "permission_mode": turn.permissions.approval_policy,
            "agent_id": turn.agent.agent_id,
            "parent_agent_id": turn.agent.parent_agent_id,
            "call_id": invocation.call_id,
            "tool_name": invocation.name,
            "tool_kind": _tool_kind(invocation.meta),
            "tool_input": dict(invocation.arguments),
        }

        if outcome is not None:
            payload["tool_outcome"] = {
                "executed": outcome.executed,
                "ok": outcome.ok,
                "duration_ms": outcome.duration_ms,
                "result": _bounded_result(outcome.result),
                "error": outcome.error,
                "cancelled": outcome.cancelled,
            }

        return payload

    @staticmethod
    def _observe_failure(
        definition: HookDefinitionConfig,
        invocation: ToolInvocation,
        error: BaseException,
    ) -> None:
        """记录 Hook 运行失败。"""
        observe_exception(
            "hook.failed",
            error,
            level="WARNING",
            hook_key=definition.key,
            hook_event=definition.event,
            tool=invocation.name,
            call_id=invocation.call_id,
        )


def _tool_kind(meta: dict[str, typing.Any] | None) -> str:
    """从工具元数据中读取稳定类别。"""
    if not isinstance(meta, dict):
        return "local"
    return str(meta.get("domain") or meta.get("class") or "local").strip() or "local"


def _pre_output_decision(data: dict[str, typing.Any]) -> tuple[bool, str]:
    """校验前置 Hook 输出并返回是否阻止及其原因。"""
    raw_decision = data.get("decision")
    if raw_decision is None:
        decision = ""
    elif isinstance(raw_decision, str):
        decision = raw_decision.strip().lower()
        if decision not in {"allow", "deny", "block"}:
            raise ValueError("hook decision must be allow, deny, or block")
    else:
        raise ValueError("hook decision must be a string")

    continuation = data.get("continue", True)
    if not isinstance(continuation, bool):
        raise ValueError("hook continue must be a boolean")

    raw_reason = data.get("reason", "")
    if not isinstance(raw_reason, str):
        raise ValueError("hook reason must be a string")

    denied = decision in {"deny", "block"} or continuation is False
    return denied, raw_reason or "tool use denied by hook"


def _bounded_result(value: typing.Any, limit: int = 32768) -> typing.Any:
    """返回适合 Hook 输入的有界结果。"""
    import json

    try:
        encoded = json.dumps(value, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= limit:
        return value
    return {
        "summary": encoded[:limit],
        "truncated": True,
    }


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回可安全回填的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
