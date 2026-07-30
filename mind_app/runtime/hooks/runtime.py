# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
import asyncio
from dataclasses import dataclass
from engine.observability import observe_exception
from mind_core.hooks import (
    HookDefinitionConfig,
    HookEventName
)
from .command import HookCommandExecutor
from .events import hook_event_spec
from .models import (
    HookDispatchResult,
    HookEventRequest,
    HookExecutionRecord,
    HookRuntimeStatus
)


class HookCommandRunner(typing.Protocol):
    """定义生命周期分发器使用的命令执行接口。"""

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


class HookDispatcher(typing.Protocol):
    """定义生命周期事件使用的不可变分发接口。"""

    def has_matching(
        self,
        event: HookEventName,
        match_value: str = "",
    ) -> bool:
        """判断指定事件是否存在匹配 Hook。"""
        ...

    async def dispatch(self, request: HookEventRequest) -> HookDispatchResult:
        """分发一次生命周期事件。"""
        ...


@dataclass(frozen=True, slots=True, init=False)
class HookRuntime:
    """匹配并执行一个轮次内固定的生命周期 Hook。"""
    command_runner: HookCommandRunner
    _definitions: tuple[HookDefinitionConfig, ...]
    _active: tuple[_RegisteredHook, ...]
    _status: HookRuntimeStatus

    def __init__(
        self,
        definitions: typing.Iterable[HookDefinitionConfig] = (),
        *,
        command_runner: HookCommandRunner | None = None,
        status: HookRuntimeStatus | None = None
    ) -> None:
        resolved = tuple(definitions)

        active_definitions = tuple(
            definition
            for definition in resolved
            if definition.enabled
        )

        active = tuple(
            _RegisteredHook(
                definition=definition,
                matcher=re.compile(definition.matcher or ".*"),
            )
            for definition in active_definitions
        )

        object.__setattr__(
            self,
            "command_runner",
            command_runner or HookCommandExecutor(),
        )
        object.__setattr__(self, "_definitions", active_definitions)
        object.__setattr__(self, "_active", active)
        object.__setattr__(
            self,
            "_status",
            status or HookRuntimeStatus(
                installed_count=len(resolved),
                active_count=len(active),
            ),
        )

    @classmethod
    def empty(cls) -> "HookRuntime":
        """返回不包含活动 Hook 的运行时。"""
        return cls()

    @property
    def installed_count(self) -> int:
        """返回已解析 Hook 数量。"""
        return self._status.installed_count

    @property
    def active_count(self) -> int:
        """返回已启用 Hook 数量。"""
        return self._status.active_count

    @property
    def definitions(self) -> tuple[HookDefinitionConfig, ...]:
        """返回当前轮次中的活动 Hook 定义。"""
        return self._definitions

    def status(self) -> HookRuntimeStatus:
        """返回当前不可变运行时状态视图。"""
        return self._status

    def has_matching(
        self,
        event: HookEventName,
        match_value: str = ""
    ) -> bool:
        """判断指定事件是否存在匹配的活动 Hook。"""
        return any(
            registered.definition.event == event
            and registered.matcher.search(match_value)
            for registered in self._active
        )

    async def dispatch(self, request: HookEventRequest) -> HookDispatchResult:
        """按事件规格执行全部匹配 Hook 并返回独立执行记录。"""
        spec = hook_event_spec(request.event)

        matching = self._matching(
            self._active,
            request.event,
            request.match_value,
        )
        if not matching:
            return HookDispatchResult(event=request.event)

        payload = {
            **request.payload,
            "hook_event_name": request.event,
        }

        tasks = tuple(
            asyncio.create_task(
                self._execute_hook(
                    registered,
                    request,
                    payload,
                    spec.normalize_output,
                ),
                name=f"hook {request.event}",
            )
            for registered in matching
        )

        try:
            records = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return HookDispatchResult(
            event=request.event,
            records=tuple(records),
        )

    async def _execute_hook(
        self,
        registered: _RegisteredHook,
        request: HookEventRequest,
        payload: dict[str, typing.Any],
        normalize_output: typing.Callable[
            [dict[str, typing.Any]],
            dict[str, typing.Any],
        ]
    ) -> HookExecutionRecord:
        """执行单个 Hook 并转换为独立执行记录。"""
        definition = registered.definition

        try:
            result = await self.command_runner.execute(
                definition,
                dict(payload),
            )

            raw_output = getattr(result, "data", None)
            if not isinstance(raw_output, dict):
                raise ValueError("hook output must be a JSON object")

            output = normalize_output(raw_output)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            self._observe_failure(definition, request, error)
            error_text = str(error).strip() or type(error).__name__
            return HookExecutionRecord(
                hook_key=definition.key,
                error=error_text,
                blocks_event=definition.on_error == "block",
            )

        return HookExecutionRecord(
            hook_key=definition.key,
            output=output,
        )

    @staticmethod
    def _matching(
        active: tuple[_RegisteredHook, ...],
        event: HookEventName,
        match_value: str
    ) -> tuple[_RegisteredHook, ...]:
        """返回事件和匹配值都满足条件的活动 Hook。"""
        return tuple(
            registered
            for registered in active
            if registered.definition.event == event
            and registered.matcher.search(match_value)
        )

    @staticmethod
    def _observe_failure(
        definition: HookDefinitionConfig,
        request: HookEventRequest,
        error: BaseException
    ) -> None:
        """记录 Hook 运行或输出校验失败。"""
        fields = {
            "hook_key": definition.key,
            "hook_event": definition.event,
        }
        fields.update({
            key: value
            for key, value in request.diagnostics.items()
            if key not in fields
        })
        observe_exception(
            "hook.failed",
            error,
            level="WARNING",
            **fields,
        )


if __name__ == '__main__':
    pass
