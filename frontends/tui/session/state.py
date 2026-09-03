# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing
from copy import deepcopy

from agent.domain.policies import (
    PermissionSettings,
    permission_label
)
from frontends.interaction import PromptContext
from infrastructure.config.preferences import apply_primary_model_override
from infrastructure.platform.workspace_context import fetch_runtime_workspace_root
from infrastructure.skills import configured_skills
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..features.context import (
    WORKSPACE_LABEL_REFRESH,
    exec_status_display_label,
    split_exec_snapshot_by_origin,
    primary_model_from_config,
    primary_model_prompt_label,
    workspace_display_label
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost


class TuiSessionState(object):
    """持有长期 TUI 会话中的模型、权限和工作区状态。"""

    def __init__(
        self,
        *,
        pref_config: dict[str, typing.Any],
        model: str,
        workspace_label: str,
        model_override: str | None = None,
        permissions: PermissionSettings
    ) -> None:
        self.pref_config = pref_config
        self.model = model
        self.model_override = model_override
        self.workspace_label = workspace_label
        self.permissions = permissions

        self._pending_prompt_extras: dict[str, typing.Any] | None = None

        self.workspace_refreshed_at = time.monotonic()

    @classmethod
    def create(
        cls,
        host: "TuiApplicationHost",
        runtime: TuiRuntime,
        *,
        model_override: str | None = None
    ) -> "TuiSessionState":
        """根据控制器缓存和已预载的运行时上下文创建会话状态。"""
        pref_config = apply_primary_model_override(
            host.settings.preference_config(),
            model_override,
        )

        return cls(
            pref_config=pref_config,
            model=primary_model_from_config(pref_config),
            model_override=model_override,
            workspace_label=runtime.context.workspace_label,
            permissions=host.settings.permissions,
        )

    def prompt_context(self) -> PromptContext:
        """生成当前输入区和 footer 使用的上下文。"""
        return PromptContext(
            model=primary_model_prompt_label(self.pref_config, self.model),
            workspace_label=self.workspace_label,
            permissions_label=permission_label(self.permissions),
        )

    def apply_prompt_context(self, runtime: TuiRuntime) -> None:
        """把当前会话上下文同步到 TUI 运行时。"""
        runtime.set_prompt_context(self.prompt_context())

    async def refresh_for_prompt(self, host: "TuiApplicationHost") -> None:
        """在等待用户输入期间刷新偏好和工作区标签。"""
        await self.refresh_preferences(host)

        now = time.monotonic()
        if now - self.workspace_refreshed_at < WORKSPACE_LABEL_REFRESH:
            return None

        runtime_workspace_root = await fetch_runtime_workspace_root()
        if runtime_workspace_root is not None:
            host.set_history_workspace(runtime_workspace_root)
            runtime = require_tui_runtime(host.frontend.runtime)
            runtime.input_model.set_workspace_root(runtime_workspace_root)

        self.workspace_label = workspace_display_label(runtime_workspace_root)
        self.workspace_refreshed_at = now

    async def refresh_preferences(
        self,
        host: "TuiApplicationHost",
        *,
        ttl_sec: float | None = None,
    ) -> dict[str, typing.Any]:
        """刷新偏好配置并保持模型字段同步。"""
        if ttl_sec is None:
            pref_config = await host.settings.fresh_preferences()
        else:
            pref_config = await host.settings.fresh_preferences(
                ttl_sec=ttl_sec,
            )

        self.pref_config = apply_primary_model_override(
            pref_config,
            self.model_override,
        )
        self.model = primary_model_from_config(self.pref_config, self.model)

        return self.pref_config

    def merge_primary(
        self,
        saved_primary: dict[str, typing.Any],
        *,
        overrides: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """合并持久化后的 primary 配置并更新当前模型。"""
        current = self.pref_config.get("primary")
        primary = dict(current) if isinstance(current, dict) else {}

        primary.update(saved_primary)

        if overrides:
            primary.update(overrides)

        if "model" in saved_primary or (
            overrides is not None and "model" in overrides
        ):
            self.model_override = None

        self.pref_config = dict(self.pref_config)
        self.pref_config["primary"] = primary
        self.model = primary_model_from_config(self.pref_config, self.model)

        return primary

    def invalidate_workspace(self) -> None:
        """让下一轮输入刷新工作区标签。"""
        self.workspace_refreshed_at = 0.0

    def replace_pending_prompt_extras(
        self,
        extras: typing.Mapping[str, typing.Any]
    ) -> None:
        """保存下一次模型提交使用的结构化扩展输入。"""
        self._pending_prompt_extras = deepcopy(dict(extras))

    def consume_pending_prompt_extras(self) -> dict[str, typing.Any]:
        """取出并清除下一次模型提交的结构化扩展输入。"""
        extras = self._pending_prompt_extras
        self._pending_prompt_extras = None
        return deepcopy(extras) if extras is not None else {}

    def clear_pending_prompt_extras(self) -> None:
        """清除尚未提交的结构化扩展输入。"""
        self._pending_prompt_extras = None


async def preload_tui_prompt_context(host: "TuiApplicationHost") -> None:
    """在主画布显示前加载输入上下文和后台进程状态。"""
    runtime = require_tui_runtime(host.frontend.runtime)

    runtime.input_model.set_skills(configured_skills(
        host.settings.config.load()
    ))
    runtime.input_model.set_workspace_root(host.history_workspace)

    pref_result, workspace_result, exec_result = await asyncio.gather(
        host.settings.fresh_preferences(ttl_sec=0.0),
        fetch_runtime_workspace_root(),
        host.workspace_runtime.coding.running_exec_sessions(),
        return_exceptions=True,
    )

    pref_config = pref_result if isinstance(pref_result, dict) else {}

    runtime_workspace_root = (
        workspace_result
        if not isinstance(workspace_result, BaseException)
        else None
    )
    exec_snapshot = exec_result if isinstance(exec_result, dict) else {}

    if runtime_workspace_root is not None:
        host.set_history_workspace(runtime_workspace_root)
        runtime.input_model.set_workspace_root(runtime_workspace_root)

    runtime.set_prompt_context(PromptContext(
        model=primary_model_prompt_label(pref_config),
        workspace_label=workspace_display_label(runtime_workspace_root),
        permissions_label=permission_label(host.settings.permissions),
    ))
    model_snapshot, user_shell_snapshot = split_exec_snapshot_by_origin(
        exec_snapshot,
    )
    runtime.set_process_status_label(exec_status_display_label(
        model_snapshot,
        line_width=runtime.terminal_width,
    ))
    runtime.set_user_shell_status_label(exec_status_display_label(
        user_shell_snapshot,
    ))
    runtime.set_background_shell_status_label(exec_status_display_label(
        exec_snapshot,
    ))

    if runtime.directory_trust_active:
        await runtime.finish_directory_trust()


if __name__ == '__main__':
    pass
