# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from observability import observe_exception
from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.context import TurnContext
from agent.domain.hooks import HookEventName
from agent.application.hooks.models import (
    HookDispatchResult,
    HookEventRequest
)
from agent.application.hooks.protocol import validate_hook_input
from agent.ports import (
    HookDispatcherPort,
    HookExecutionScopePort,
    HookScopeProviderPort,
    HookStatusPort,
)
from agent.harness.hooks.runtime import HookRuntime


@dataclass(frozen=True, slots=True)
class HookExecutionScope:
    """在一个执行作用域内固定 Hook 运行时和公共上下文。"""
    context: HookExecutionContext
    dispatcher: HookDispatcherPort

    @classmethod
    def empty(cls, context: HookExecutionContext) -> "HookExecutionScope":
        """返回不包含活动 Hook 的执行作用域。"""
        return cls(context=context, dispatcher=HookRuntime.empty())

    def has_matching(
        self,
        event: HookEventName,
        match_value: str = ""
    ) -> bool:
        """判断当前作用域是否存在匹配 Hook。"""
        return self.dispatcher.has_matching(event, match_value)

    async def dispatch(
        self,
        event: HookEventName,
        *,
        payload: dict[str, typing.Any] | None = None,
        match_value: str = "",
        diagnostics: dict[str, typing.Any] | None = None
    ) -> HookDispatchResult:
        """合并公共上下文并分发一次生命周期事件。"""
        event_payload = self.context.payload(event, payload)
        validate_hook_input(event, event_payload)

        return await self.dispatcher.dispatch(HookEventRequest(
            event=event,
            payload=event_payload,
            match_value=match_value,
            diagnostics=dict(diagnostics or {}),
        ))

    def with_default_status_port(
        self,
        status_port: HookStatusPort
    ) -> "HookExecutionScope":
        """在分发器尚未配置展示端时绑定单轮状态端口。"""
        dispatcher = self.dispatcher.with_default_status_port(status_port)
        if dispatcher is self.dispatcher:
            return self
        return type(self)(context=self.context, dispatcher=dispatcher)

    def require_turn(self, turn: TurnContext) -> None:
        """验证模型轮次属于当前固定作用域。"""
        if HookExecutionContext.from_turn(turn) != self.context:
            raise ValueError("turn does not belong to hook scope")

    def for_turn(self, turn: TurnContext) -> "HookExecutionScope":
        """保留 dispatcher 并创建绑定新 Turn 的执行作用域。"""
        return type(self)(
            context=HookExecutionContext.from_turn(turn),
            dispatcher=self.dispatcher,
        )


def resolve_hook_scope(
    provider: HookScopeProviderPort,
    context: TurnContext,
) -> HookExecutionScopePort:
    """解析宿主提供的 Hook 作用域，并在配置失败时返回空作用域。"""
    hook_context = HookExecutionContext.from_turn(context)

    try:
        scope = provider.hook_scope(hook_context)
        if not isinstance(scope, HookExecutionScopePort):
            raise TypeError("hook scope provider returned an invalid scope")
        return scope
    except (OSError, TypeError, ValueError) as error:
        observe_exception(
            "hooks.resolve.failed",
            error,
            level="WARNING",
        )
        return HookExecutionScope.empty(hook_context)


if __name__ == '__main__':
    pass
