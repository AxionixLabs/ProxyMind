# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from collections.abc import (
    Callable,
    Iterable,
)
from pathlib import Path

from agent.application.hooks.catalog import (
    HookCatalogSnapshot,
    HookCatalogStaleError,
)
from agent.application.hooks.context import HookExecutionContext
from agent.domain.hooks import HookDefinitionConfig
from agent.harness.hooks.scope import HookExecutionScope
from agent.ports import (
    HookRegistryPort,
    HookStatusPort,
)
from infrastructure.config.session import ConfigSession

WorkspaceProvider: typing.TypeAlias = Callable[[], str | Path]


class HookManager:
    """拥有配置化 Hook 的清单、执行作用域和底层资源生命周期。"""

    def __init__(
        self,
        config_session: ConfigSession,
        registry: HookRegistryPort,
        *,
        workspace: WorkspaceProvider,
        status_port: HookStatusPort | None = None,
    ) -> None:
        self._config_session = config_session
        self._registry = registry
        self._workspace = workspace
        self._status_port = status_port

    def _resolve_workspace(self, workspace: Path | None = None) -> Path:
        """返回查询和状态更新使用的绝对工作区。"""
        target = workspace if workspace is not None else self._workspace()
        return Path(target).expanduser().resolve()

    def _state_target(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        workspace: Path | None,
    ) -> tuple[Path, HookDefinitionConfig]:
        """解析并校验允许修改用户状态的 Hook。"""
        target_workspace = self._resolve_workspace(workspace)
        resolution = self._config_session.resolve(workspace=target_workspace)
        definition = next(
            (item for item in resolution.hooks if item.key == hook_key),
            None,
        )
        if definition is None:
            raise HookCatalogStaleError(f"hook is unavailable: {hook_key}")

        expected_hash = str(expected_content_hash or "").strip().lower()
        if definition.content_hash != expected_hash:
            raise HookCatalogStaleError(f"hook content changed: {hook_key}")
        return target_workspace, definition

    def hook_scope(
        self,
        context: HookExecutionContext,
    ) -> HookExecutionScope:
        """从当前配置快照创建绑定执行上下文的 Hook 作用域。"""
        workspace = self._resolve_workspace(Path(context.cwd))
        resolution = self._config_session.resolve(workspace=workspace)
        return HookExecutionScope(
            context=context,
            dispatcher=self._registry.build(
                resolution.hooks,
                hook_states=resolution.hook_states,
                warnings=resolution.hook_warnings,
                status_port=self._status_port,
            ),
        )

    def inspect(
        self,
        *,
        workspace: Path | None = None,
    ) -> HookCatalogSnapshot:
        """返回指定工作区的实时 Hook 管理快照。"""
        target_workspace = self._resolve_workspace(workspace)
        resolution = self._config_session.resolve(workspace=target_workspace)
        return self._registry.inspect(
            resolution.hooks,
            hook_states=resolution.hook_states,
            warnings=resolution.hook_warnings,
            workspace=target_workspace,
        )

    def trust(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        workspace: Path | None = None,
    ) -> HookCatalogSnapshot:
        """信任指定 Hook 的当前内容。"""
        return self.trust_many(
            ((hook_key, expected_content_hash),),
            workspace=workspace,
        )

    def trust_many(
        self,
        hooks: Iterable[tuple[str, str]],
        *,
        workspace: Path | None = None,
    ) -> HookCatalogSnapshot:
        """校验并批量信任多个 Hook 的当前内容。"""
        target_workspace = self._resolve_workspace(workspace)
        resolution = self._config_session.resolve(workspace=target_workspace)
        definitions = {
            definition.key: definition
            for definition in resolution.hooks
        }
        updates: dict[tuple[str, ...], str] = {}

        for hook_key, expected_content_hash in hooks:
            definition = definitions.get(hook_key)
            if definition is None:
                raise HookCatalogStaleError(
                    f"hook is unavailable: {hook_key}"
                )
            expected_hash = str(expected_content_hash or "").strip().lower()
            if definition.content_hash != expected_hash:
                raise HookCatalogStaleError(
                    f"hook content changed: {hook_key}"
                )
            if definition.trust_policy != "content_hash":
                raise ValueError(
                    f"{definition.trust_policy} hook trust cannot be changed"
                )
            updates[(
                "hooks",
                "state",
                definition.key,
                "trusted_hash",
            )] = definition.content_hash

        if updates:
            self._config_session.update_user(updates)
        return self.inspect(workspace=target_workspace)

    def set_enabled(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        enabled: bool,
        workspace: Path | None = None,
    ) -> HookCatalogSnapshot:
        """更新指定 Hook 的独立启用状态。"""
        target_workspace, definition = self._state_target(
            hook_key,
            expected_content_hash=expected_content_hash,
            workspace=workspace,
        )
        if definition.trust_policy == "managed":
            raise ValueError("managed hook enabled state cannot be changed")

        self._config_session.update_user({
            (
                "hooks",
                "state",
                definition.key,
                "enabled",
            ): bool(enabled),
        })
        return self.inspect(workspace=target_workspace)

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定会话产生的 Hook 临时资源。"""
        await self._registry.cleanup_session(session_id)

    async def close(self) -> None:
        """关闭当前管理器拥有的 Hook 执行资源。"""
        await self._registry.close()


if __name__ == "__main__":
    pass
