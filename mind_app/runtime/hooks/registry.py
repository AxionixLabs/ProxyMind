# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from pathlib import Path
from engine.observability import observe_exception
from mind_core.hook_trust import (
    HookTrustSnapshot,
    HookTrustState,
    HookTrustStore,
    HookTrustStoreError
)
from mind_core.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HookDefinitionConfig
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
    HookRuntime
)


@dataclass(frozen=True, slots=True)
class _ResolvedHook:
    """保存一次信任解析得到的 Hook 激活状态。"""
    definition: HookDefinitionConfig
    trust_state: HookTrustState
    active: bool


class HookRegistry:
    """解析信任状态并为单个轮次构建不可变 Hook 运行时。"""

    def __init__(
        self,
        *,
        trust_store: HookTrustStore | None = None,
        command_runner: HookCommandRunner | None = None
    ) -> None:
        self._trust_store    = trust_store
        self._command_runner = command_runner or HookCommandExecutor()

    def build(
        self,
        definitions: typing.Iterable[HookDefinitionConfig]
    ) -> HookRuntime:
        """按当前信任状态构建一个独立运行时。"""
        resolved, trust_error = self._resolve(definitions)

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
            trust_error=trust_error,
        )

        return HookRuntime(
            active,
            command_runner=self._command_runner,
            status=status,
        )

    def inspect(
        self,
        definitions: typing.Iterable[HookDefinitionConfig],
        *,
        workspace: Path
    ) -> HookCatalogSnapshot:
        """返回指定工作区的 Hook 管理快照。"""
        resolved, trust_error = self._resolve(definitions)

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
            trust_error=trust_error,
        )

    def trust(self, definition: HookDefinitionConfig) -> None:
        """持久信任指定 Hook 的当前内容。"""
        self._require_trust_store().trust(definition)

    def revoke(self, definition: HookDefinitionConfig) -> None:
        """撤销指定 Hook 的持久信任。"""
        self._require_trust_store().revoke(definition)

    def _resolve(
        self,
        definitions: typing.Iterable[HookDefinitionConfig]
    ) -> tuple[tuple[_ResolvedHook, ...], str]:
        """统一解析 Hook 信任和激活状态。"""
        trust, trust_error = self._load_trust()

        resolved: list[_ResolvedHook] = []

        for definition in definitions:
            trust_state = trust.state(definition)
            resolved.append(_ResolvedHook(
                definition=definition,
                trust_state=trust_state,
                active=(
                    definition.enabled
                    and trust_state != "untrusted"
                ),
            ))

        return tuple(resolved), trust_error

    @staticmethod
    def _runtime_entry(item: _ResolvedHook) -> HookRuntimeEntry:
        """把解析结果转换为执行状态条目。"""
        definition = item.definition

        return HookRuntimeEntry(
            key=definition.key,
            event=definition.event,
            source_scope=definition.source_scope,
            source_path=definition.source_path,
            content_hash=definition.content_hash,
            enabled=definition.enabled,
            trust_state=item.trust_state,
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
            command=definition.command,
            matcher=definition.matcher,
            matcher_subject=event_spec.matcher_subject,
            timeout_sec=definition.timeout_sec,
            on_error=definition.on_error,
            source_scope=definition.source_scope,
            source_path=definition.source_path,
            enabled=definition.enabled,
            trust_state=item.trust_state,
            active=item.active,
            content_hash=definition.content_hash,
        )

    def _load_trust(self) -> tuple[HookTrustSnapshot, str]:
        """读取信任记录，失败时返回空显式信任。"""
        if self._trust_store is None:
            return HookTrustSnapshot(), ""
        try:
            return self._trust_store.load(), ""
        except HookTrustStoreError as error:
            observe_exception(
                "hook.trust.load.failed",
                error,
                level="WARNING",
            )
            return HookTrustSnapshot(), str(error)

    def _require_trust_store(self) -> HookTrustStore:
        """返回可写信任存储。"""
        if self._trust_store is None:
            raise HookTrustStoreError("hook trust store is unavailable")
        return self._trust_store


if __name__ == '__main__':
    pass
