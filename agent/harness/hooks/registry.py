# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from pathlib import Path

from agent.application.hooks.catalog import (
    HookCatalogEntry,
    HookCatalogSnapshot,
    HookEventSummary,
)
from agent.application.hooks.models import (
    HookRuntimeEntry,
    HookRuntimeStatus,
)
from agent.domain.hook_trust import (
    HookTrustState,
    hook_needs_review,
    resolve_hook_state,
)
from agent.domain.hooks import (
    CommandHookHandlerConfig,
    HOOK_EVENT_CONFIG_SPECS,
    HOOK_EVENT_NAMES,
    HookDefinitionConfig,
    HookStateTable,
    McpToolHookHandlerConfig,
)
from agent.ports import (
    HookCommandRunner,
    HookAsyncTaskOwnerPort,
    HookContextSpiller,
    HookDispatcherPort,
    HookMcpRunner,
    HookMcpRunnerBinder,
    HookResourceClose,
    HookStatusPort,
    HookSessionCleanup,
)
from observability import observe
from .runtime import HookRuntime


@dataclass(frozen=True, slots=True)
class _ResolvedHook:
    """保存一次信任解析得到的 Hook 激活状态。"""
    definition: HookDefinitionConfig
    trust_state: HookTrustState
    enabled: bool
    active: bool


class HookRegistry(HookMcpRunnerBinder):
    """解析信任状态并为单个轮次构建不可变 Hook 运行时。"""

    def __init__(
        self,
        *,
        command_runner: HookCommandRunner | None = None,
        mcp_runner: HookMcpRunner | None = None,
        async_task_owner: HookAsyncTaskOwnerPort | None = None,
        context_spiller: HookContextSpiller | None = None,
        cleanup_session: HookSessionCleanup | None = None,
        close: HookResourceClose | None = None,
        bypass_hook_trust: bool = False
    ) -> None:
        self._command_runner = command_runner
        self._mcp_runner = mcp_runner
        self._async_task_owner = async_task_owner
        self._context_spiller = context_spiller
        self._cleanup_session = cleanup_session
        self._close = close
        self._bypass_hook_trust = bool(bypass_hook_trust)

        self._observed_warnings: set[str] = set()

    def bind_mcp_runner(self, runner: HookMcpRunner) -> None:
        """绑定复用现有 MCP owner 的 Hook 适配器。"""
        if not isinstance(runner, HookMcpRunner):
            raise TypeError("hook MCP runner is invalid")
        if self._mcp_runner is not None:
            raise RuntimeError("hook MCP runner is already bound")
        self._mcp_runner = runner

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
    def _catalog_entry(
        item: _ResolvedHook,
        *,
        display_order: int
    ) -> HookCatalogEntry:
        """把解析结果转换为管理视图条目。"""
        definition = item.definition
        event_spec = HOOK_EVENT_CONFIG_SPECS[definition.event]

        return HookCatalogEntry(
            key=definition.key,
            event=definition.event,
            handler_type=definition.handler.type,
            command=(
                definition.handler.command
                if isinstance(definition.handler, CommandHookHandlerConfig)
                else None
            ),
            command_windows=(
                definition.handler.command_windows
                if isinstance(definition.handler, CommandHookHandlerConfig)
                else None
            ),
            status_message=definition.handler.status_message,
            mcp_server=(
                definition.handler.server
                if isinstance(definition.handler, McpToolHookHandlerConfig)
                else None
            ),
            mcp_tool=(
                definition.handler.tool
                if isinstance(definition.handler, McpToolHookHandlerConfig)
                else None
            ),
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
            display_order=display_order,
        )

    @staticmethod
    def _unsupported_warnings(
        resolved: typing.Iterable[_ResolvedHook]
    ) -> tuple[str, ...]:
        """返回当前可执行 handler 不需要的兼容告警。"""
        del resolved
        return ()

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

    def _resolve(
        self,
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
                active=(
                    state.active
                    or (self._bypass_hook_trust and state.enabled)
                ),
            ))

        return tuple(resolved)

    def startup_warnings(
        self,
        definitions: typing.Iterable[HookDefinitionConfig],
        *,
        hook_states: HookStateTable | None = None,
        warnings: typing.Iterable[str] = (),
    ) -> tuple[str, ...]:
        """返回当前非交互 Hook 初始化需要报告的告警。"""
        resolved = self._resolve(definitions, hook_states or {})
        warning_items = list(warnings)
        warning_items.extend(
            warning
            for warning in self._unsupported_warnings(resolved)
            if warning not in warning_items
        )
        result = tuple(warning_items)
        self._observe_warnings(result)
        return result

    def build(
        self,
        definitions: typing.Iterable[HookDefinitionConfig],
        *,
        hook_states: HookStateTable | None = None,
        warnings: typing.Iterable[str] = (),
        status_port: HookStatusPort | None = None
    ) -> HookDispatcherPort:
        """按当前信任状态构建一个独立运行时。"""
        resolved = self._resolve(definitions, hook_states or {})
        warning_items = list(warnings)

        warning_items.extend(
            warning
            for warning in self._unsupported_warnings(resolved)
            if warning not in warning_items
        )

        warning_items = tuple(warning_items)
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

        active_types = {definition.handler.type for definition in active}
        if "command" in active_types and self._command_runner is None:
            raise TypeError("Hook command runner is required for active hooks")
        if "mcp_tool" in active_types and self._mcp_runner is None:
            raise TypeError("Hook MCP runner is required for active hooks")
        if (
            any(
                definition.handler.run_async
                and definition.event != "SessionEnd"
                for definition in active
            )
            and self._async_task_owner is None
        ):
            raise TypeError("Hook async task owner is required for active hooks")

        return HookRuntime(
            active,
            command_runner=self._command_runner,
            mcp_runner=self._mcp_runner,
            async_task_owner=self._async_task_owner,
            context_spiller=self._context_spiller,
            status_port=status_port,
            status=status,
        )

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
            self._catalog_entry(item, display_order=display_order)
            for display_order, item in enumerate(resolved)
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
                review_count=sum(
                    item.definition.event == event
                    and hook_needs_review(item.trust_state)
                    for item in resolved
                ),
            )
            for event in HOOK_EVENT_NAMES
            for spec in (HOOK_EVENT_CONFIG_SPECS[event],)
        )

        return HookCatalogSnapshot(
            workspace=str(Path(workspace).expanduser().resolve()),
            installed_count=len(hooks),
            active_count=sum(item.active for item in resolved),
            events=events,
            hooks=hooks,
            warnings=warning_items,
        )

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定会话产生的 Hook 临时输出。"""
        cleanup_session = self._cleanup_session
        if cleanup_session is not None:
            await cleanup_session(session_id)

    async def close(self) -> None:
        """关闭 Hook 命令执行器持有的临时资源。"""
        if self._async_task_owner is not None:
            await self._async_task_owner.close()
        close = self._close
        if close is not None:
            await close()


if __name__ == '__main__':
    pass
