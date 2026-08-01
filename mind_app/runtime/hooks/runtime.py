# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import itertools
from dataclasses import (
    dataclass,
    replace
)
from engine.observability import (
    observe,
    observe_exception
)
from mind_core.hooks import (
    HookDefinitionConfig,
    HookEventName
)
from .command import HookCommandExecutor
from .effects import normalize_business_block
from .events import hook_event_spec
from .matching import (
    HookMatcher,
    compile_hook_matcher,
    hook_match_candidates
)
from .models import (
    HookDispatchResult,
    HookEventRequest,
    HookExecutionRecord,
    HookNormalizedOutput,
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


class HookContextSpiller(typing.Protocol):
    """定义过大 Hook 上下文的会话级落盘接口。"""

    async def spill_context(
        self,
        text: str,
        *,
        session_id: str,
    ) -> str:
        """写入完整上下文并返回模型可见的恢复摘要。"""
        ...


class HookStatusPort(typing.Protocol):
    """定义 Hook 命令状态消息的展示生命周期。"""

    async def started(self, key: str, message: str) -> None:
        """显示一个 Hook 命令已经开始。"""
        ...

    async def completed(self, key: str) -> None:
        """结束一个 Hook 命令的状态展示。"""
        ...


@dataclass(frozen=True, slots=True)
class _RegisteredHook:
    """保存已编译 matcher 的活动 Hook。"""
    definition: HookDefinitionConfig
    matcher: HookMatcher


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
    context_spiller: HookContextSpiller | None
    status_port: HookStatusPort | None
    _definitions: tuple[HookDefinitionConfig, ...]
    _active: tuple[_RegisteredHook, ...]
    _status: HookRuntimeStatus
    _completion_order: typing.Iterator[int]

    def __init__(
        self,
        definitions: typing.Iterable[HookDefinitionConfig] = (),
        *,
        command_runner: HookCommandRunner | None = None,
        context_spiller: HookContextSpiller | None = None,
        status_port: HookStatusPort | None = None,
        status: HookRuntimeStatus | None = None
    ) -> None:
        resolved = tuple(definitions)
        active_definitions = resolved

        active = tuple(
            _RegisteredHook(
                definition=definition,
                matcher=compile_hook_matcher(
                    definition.event,
                    definition.matcher,
                ),
            )
            for definition in active_definitions
        )

        resolved_runner = command_runner or HookCommandExecutor()
        object.__setattr__(self, "command_runner", resolved_runner)
        object.__setattr__(
            self,
            "context_spiller",
            (
                context_spiller
                if context_spiller is not None
                else resolved_runner
                if isinstance(resolved_runner, HookCommandExecutor)
                else None
            ),
        )
        object.__setattr__(self, "status_port", status_port)
        object.__setattr__(self, "_definitions", active_definitions)
        object.__setattr__(self, "_active", active)
        object.__setattr__(self, "_completion_order", itertools.count(1))
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
            and registered.matcher.matches(
                hook_match_candidates(event, match_value)
            )
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
            HookNormalizedOutput,
        ]
    ) -> HookExecutionRecord:
        """按当前支持范围执行单个 Hook。"""
        definition = registered.definition

        if definition.handler.run_async and definition.event != "SessionEnd":
            self._observe_unsupported_async(definition)
            return HookExecutionRecord(
                hook_key=definition.key,
                completion_order=next(self._completion_order),
            )

        await self._status_started(definition)

        try:
            record = await self._execute_hook_wait(
                registered,
                request,
                payload,
                normalize_output,
            )

            return replace(
                record,
                completion_order=next(self._completion_order),
            )

        finally:
            await self._status_completed(definition)

    async def _execute_hook_wait(
        self,
        registered: _RegisteredHook,
        request: HookEventRequest,
        payload: dict[str, typing.Any],
        normalize_output: typing.Callable[
            [dict[str, typing.Any]],
            HookNormalizedOutput,
        ]
    ) -> HookExecutionRecord:
        """执行单个 Hook 并转换为独立执行记录。"""
        definition  = registered.definition
        stderr_text = ""

        try:
            result = await self.command_runner.execute(
                definition,
                dict(payload),
            )

            raw_output = getattr(result, "data", None)
            if not isinstance(raw_output, dict):
                raise ValueError("hook output must be a JSON object")

            stderr_text = str(getattr(result, "stderr", "") or "").strip()
            if stderr_text:
                self._observe_stderr(definition, request, stderr_text)

            if bool(getattr(result, "business_block", False)):
                normalized = normalize_business_block(
                    request.event,
                    reason=str(getattr(result, "block_reason", "") or ""),
                    transport_output=raw_output,
                )
            else:
                normalized = normalize_output(raw_output)

            normalized = await self._limit_additional_context(
                definition,
                payload,
                normalized,
            )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            self._observe_failure(definition, request, error)
            error_text = str(error).strip() or type(error).__name__
            return HookExecutionRecord(
                hook_key=definition.key,
                stderr=stderr_text,
                error=error_text,
            )

        return HookExecutionRecord(
            hook_key=definition.key,
            output=normalized.output,
            effect=normalized.effect,
            stderr=stderr_text,
        )

    async def _status_started(
        self,
        definition: HookDefinitionConfig
    ) -> None:
        """显示处理器配置的非空状态消息。"""
        status  = self.status_port
        message = definition.handler.status_message

        if status is None or not message:
            return None

        try:
            await status.started(definition.key, message)
        except Exception as error:
            observe_exception(
                "hook.status.failed",
                error,
                level="WARNING",
                hook_key=definition.key,
                hook_event=definition.event,
            )

    async def _status_completed(
        self,
        definition: HookDefinitionConfig
    ) -> None:
        """结束处理器状态消息且不影响 Hook 主结果。"""
        status = self.status_port
        if status is None or not definition.handler.status_message:
            return None

        try:
            await status.completed(definition.key)
        except Exception as error:
            observe_exception(
                "hook.status.failed",
                error,
                level="WARNING",
                hook_key=definition.key,
                hook_event=definition.event,
            )

    async def _limit_additional_context(
        self,
        definition: HookDefinitionConfig,
        payload: dict[str, typing.Any],
        normalized: HookNormalizedOutput
    ) -> HookNormalizedOutput:
        """按处理器阈值把过大的附加上下文写入临时文件。"""
        contexts = normalized.effect.additional_context
        limit    = definition.handler.additional_context_limit
        spiller  = self.context_spiller

        if not contexts or limit == 0 or spiller is None:
            return normalized

        full_text = "\n\n".join(contexts)

        approximate_tokens = (len(full_text) + 3) // 4
        if approximate_tokens <= limit:
            return normalized

        summary = await spiller.spill_context(
            full_text,
            session_id=str(payload.get("session_id") or ""),
        )

        output = _replace_additional_context_output(
            normalized.output,
            summary,
        )

        return HookNormalizedOutput(
            output=output,
            effect=replace(
                normalized.effect,
                additional_context=(summary,),
            ),
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
            and registered.matcher.matches(
                hook_match_candidates(event, match_value)
            )
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

    @staticmethod
    def _observe_unsupported_async(
        definition: HookDefinitionConfig
    ) -> None:
        """记录当前事件不支持异步命令处理器。"""
        observe(
            "hook.async_unsupported",
            level="WARNING",
            hook_key=definition.key,
            hook_event=definition.event,
        )

    @staticmethod
    def _observe_stderr(
        definition: HookDefinitionConfig,
        request: HookEventRequest,
        stderr: str
    ) -> None:
        """把命令标准错误写入 Hook 审计事件。"""
        fields = {
            "hook_key"   : definition.key,
            "hook_event" : definition.event,
            "stderr"     : stderr[:8192]
        }

        fields.update({
            key: value
            for key, value in request.diagnostics.items()
            if key not in fields
        })

        observe("hook.stderr", level="WARNING", **fields)


def _replace_additional_context_output(
    output: dict[str, typing.Any],
    summary: str
) -> dict[str, typing.Any]:
    """用落盘恢复摘要替换执行记录中的完整附加上下文。"""
    replaced = dict(output)
    for key in ("additionalContext", "additional_context"):
        if key in replaced:
            replaced[key] = summary

    specific = replaced.get("hookSpecificOutput")
    if isinstance(specific, dict):
        specific_copy = dict(specific)
        for key in ("additionalContext", "additional_context"):
            if key in specific_copy:
                specific_copy[key] = summary
        replaced["hookSpecificOutput"] = specific_copy

    replaced["additional_context"] = summary

    return replaced


if __name__ == '__main__':
    pass
