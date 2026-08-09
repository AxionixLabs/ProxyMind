# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from pathlib import Path
from engine.observability import observe
from mind_core.hook_trust import (
    HookTrustState,
    resolve_hook_state
)
from mind_core.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HookDefinitionConfig,
    HookStateTable
)
from .catalog import (
    HookCatalogEntry,
    HookCatalogSnapshot,
    HookEventSummary
)
from .command import HookCommandExecutor
from .models import (
    HookRuntimeEntry,
    HookRuntimeStatus
)
from .runtime import (
    HookCommandRunner,
    HookRuntime,
    HookStatusPort
)


@dataclass(frozen=True, slots=True)
class _ResolvedHook:
    """保存一次信任解析得到的 Hook 激活状态。"""
    definition: HookDefinitionConfig
    trust_state: HookTrustState
    enabled: bool
    active: bool


class HookRegistry:
    """解析信任状态并为单个轮次构建不可变 Hook 运行时。"""

    def __init__(
        self,
        *,
        command_runner: HookCommandRunner | None = None
    ) -> None:
        self._command_runner = command_runner or HookCommandExecutor()
        self._observed_warnings: set[str] = set()

    def build(
        self,
        definitions: typing.Iterable[HookDefinitionConfig],
        *,
        hook_states: HookStateTable | None = None,
        warnings: typing.Iterable[str] = (),
        status_port: HookStatusPort | None = None
    ) -> HookRuntime:
        """按当前信任状态构建一个独立运行时。"""
        resolved = self._resolve(definitions, hook_states or {})
        warning_items = tuple(warnings)
        self._observe_warnings(warning_items)

        active = tuple(
            item.definition
            for item in resolved
            if item.active
        )

        status = HookRuntimeStatus(
            installed_count=len(resolved),
            active_count=len(active),
            hooks=tuple(
                self._runtime_entry(item)
                for item in resolved
            ),
            warnings=warning_items,
        )

        return HookRuntime(
            active,
            command_runner=self._command_runner,
            context_spiller=(
                self._command_runner
                if isinstance(self._command_runner, HookCommandExecutor)
                else None
            ),
            status_port=status_port,
            status=status,
        )

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定会话产生的 Hook 临时输出。"""
        if isinstance(self._command_runner, HookCommandExecutor):
            await self._command_runner.cleanup_session(session_id)

    async def close(self) -> None:
        """关闭 Hook 命令执行器持有的临时资源。"""
        if isinstance(self._command_runner, HookCommandExecutor):
            await self._command_runner.close()

    def inspect(
        self,
        definitions: typing.Iterable[HookDefinitionConfig],
        *,
        hook_states: HookStateTable | None = None,
        warnings: typing.Iterable[str] = (),
        workspace: Path
    ) -> HookCatalogSnapshot:
        """返回指定工作区的 Hook 管理快照。"""
        resolved = self._resolve(definitions, hook_states or {})
        warning_items = tuple(warnings)
        self._observe_warnings(warning_items)

        hooks = tuple(
            self._catalog_entry(item)
            for item in resolved
        )

        events = tuple(
            HookEventSummary(
                event=event,
                description=spec.description,
                matcher_subject=spec.matcher_subject,
                control_policy=spec.control_policy,
                installed_count=sum(
                    item.definition.event == event
                    for item in resolved
                ),
                active_count=sum(
                    item.definition.event == event and item.active
                    for item in resolved
                ),
            )
            for event, spec in HOOK_EVENT_CONFIG_SPECS.items()
        )

        return HookCatalogSnapshot(
            workspace=str(Path(workspace).expanduser().resolve()),
            installed_count=len(hooks),
            active_count=sum(item.active for item in resolved),
            events=events,
            hooks=hooks,
            warnings=warning_items,
        )

    def _observe_warnings(self, warnings: tuple[str, ...]) -> None:
        """记录当前进程中尚未报告过的 discovery warning。"""
        for warning in warnings:
            if warning in self._observed_warnings:
                continue
            self._observed_warnings.add(warning)
            observe(
                "hook.discovery.warning",
                level="WARNING",
                warning=warning,
            )

    @staticmethod
    def _resolve(
        definitions: typing.Iterable[HookDefinitionConfig],
        hook_states: HookStateTable
    ) -> tuple[_ResolvedHook, ...]:
        """统一解析 Hook 信任和激活状态。"""
        resolved: list[_ResolvedHook] = []

        for definition in definitions:
            state = resolve_hook_state(definition, hook_states)
            resolved.append(_ResolvedHook(
                definition=definition,
                trust_state=state.trust_state,
                enabled=state.enabled,
                active=state.active,
            ))

        return tuple(resolved)

    @staticmethod
    def _runtime_entry(item: _ResolvedHook) -> HookRuntimeEntry:
        """把解析结果转换为执行状态条目。"""
        definition = item.definition

        return HookRuntimeEntry(
            key=definition.key,
            event=definition.event,
            source_scope=definition.source_scope,
            source_path=definition.source_path,
            trust_policy=definition.trust_policy,
            content_hash=definition.content_hash,
            trust_state=item.trust_state,
            enabled=item.enabled,
            active=item.active,
        )

    @staticmethod
    def _catalog_entry(item: _ResolvedHook) -> HookCatalogEntry:
        """把解析结果转换为管理视图条目。"""
        definition = item.definition
        event_spec = HOOK_EVENT_CONFIG_SPECS[definition.event]

        return HookCatalogEntry(
            key=definition.key,
            event=definition.event,
            command=definition.handler.command,
            command_windows=definition.handler.command_windows,
            status_message=definition.handler.status_message,
            matcher=definition.matcher,
            matcher_subject=event_spec.matcher_subject,
            timeout_sec=definition.handler.timeout_sec,
            run_async=definition.handler.run_async,
            additional_context_limit=(
                definition.handler.additional_context_limit
            ),
            source_scope=definition.source_scope,
            source_path=definition.source_path,
            trust_policy=definition.trust_policy,
            trust_state=item.trust_state,
            enabled=item.enabled,
            active=item.active,
            content_hash=definition.content_hash,
        )


if __name__ == '__main__':
    pass
