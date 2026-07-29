# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.observability import observe_exception
from mind_core.hook_trust import (
    HookTrustSnapshot,
    HookTrustStore,
    HookTrustStoreError
)
from mind_core.hooks import HookDefinitionConfig
from .command import HookCommandExecutor
from .models import (
    HookRuntimeEntry,
    HookRuntimeStatus
)
from .runtime import (
    HookCommandRunner,
    HookRuntime
)


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
        resolved = tuple(definitions)

        trust, trust_error = self._load_trust()

        active: list[HookDefinitionConfig] = []
        entries: list[HookRuntimeEntry]    = []

        for definition in resolved:
            trust_state = trust.state(definition)
            is_active   = definition.enabled and trust_state != "untrusted"

            if is_active:
                active.append(definition)

            entries.append(HookRuntimeEntry(
                key=definition.key,
                event=definition.event,
                source_scope=definition.source_scope,
                source_path=definition.source_path,
                content_hash=definition.content_hash,
                enabled=definition.enabled,
                trust_state=trust_state,
                active=is_active,
            ))

        status = HookRuntimeStatus(
            installed_count=len(resolved),
            active_count=len(active),
            hooks=tuple(entries),
            trust_error=trust_error,
        )

        return HookRuntime(
            active,
            command_runner=self._command_runner,
            status=status,
        )

    def trust(self, definition: HookDefinitionConfig) -> None:
        """持久信任指定 Hook 的当前内容。"""
        self._require_trust_store().trust(definition)

    def revoke(self, definition: HookDefinitionConfig) -> None:
        """撤销指定 Hook 的持久信任。"""
        self._require_trust_store().revoke(definition)

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
