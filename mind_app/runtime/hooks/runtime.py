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
    HookExecutionRecord
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


class HookRuntime:
    """匹配并执行当前进程启用的生命周期 Hook。"""

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

        matching = self._matching(request.event, request.match_value)
        if not matching:
            return HookDispatchResult(event=request.event)

        payload = {
            **request.payload,
            "hook_event_name": request.event,
        }

        records: list[HookExecutionRecord] = []

        for registered in matching:
            definition = registered.definition

            try:
                result = await self.command_runner.execute(
                    definition,
                    dict(payload),
                )

                raw_output = getattr(result, "data", None)
                if not isinstance(raw_output, dict):
                    raise ValueError("hook output must be a JSON object")

                output = spec.normalize_output(raw_output)

            except asyncio.CancelledError:
                raise

            except Exception as error:
                self._observe_failure(definition, request, error)
                error_text = str(error).strip() or type(error).__name__
                records.append(HookExecutionRecord(
                    hook_key=definition.key,
                    error=error_text,
                    blocks_event=definition.on_error == "block",
                ))
                continue

            records.append(HookExecutionRecord(
                hook_key=definition.key,
                output=output,
            ))

        return HookDispatchResult(
            event=request.event,
            records=tuple(records),
        )

    def _matching(
        self,
        event: HookEventName,
        match_value: str
    ) -> tuple[_RegisteredHook, ...]:
        """返回事件和匹配值都满足条件的活动 Hook。"""
        return tuple(
            registered
            for registered in self._active
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
