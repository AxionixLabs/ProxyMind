# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.interaction import PromptContext
from mind_app.runtime.environment.workspace import fetch_runtime_workspace_root
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.requests.access import (
    DEFAULT_ACCESS_MODE,
    access_mode_label
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..features.context import (
    WORKSPACE_LABEL_REFRESH,
    exec_status_display_label,
    primary_model_from_config,
    primary_model_prompt_label,
    workspace_display_label
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


class TuiSessionState(object):
    """持有长期 TUI 会话中的模型、模式、权限和工作区状态。"""

    def __init__(
        self,
        *,
        pref_config: dict[str, typing.Any],
        model: str,
        workspace_label: str,
        model_override: str | None = None,
        mode: RunMode = DEFAULT_RUN_MODE,
        access_mode: str = DEFAULT_ACCESS_MODE,
    ) -> None:
        self.pref_config     = pref_config
        self.model           = model
        self.model_override  = model_override
        self.workspace_label = workspace_label
        self.mode            = mode
        self.access_mode     = access_mode

        self.workspace_refreshed_at = time.monotonic()

    @classmethod
    def create(
        cls,
        mind: "Mind",
        runtime: TuiRuntime,
        *,
        model_override: str | None = None,
    ) -> "TuiSessionState":
        """根据控制器缓存和已预载的运行时上下文创建会话状态。"""
        pref_config = cls._apply_model_override(
            mind.pref.to_config(),
            model_override,
        )

        return cls(
            pref_config=pref_config,
            model=primary_model_from_config(pref_config),
            model_override=model_override,
            workspace_label=runtime.context.workspace_label,
        )

    @staticmethod
    def _apply_model_override(
        pref_config: dict[str, typing.Any],
        model: str | None,
    ) -> dict[str, typing.Any]:
        """把临时模型选择合并到当前会话配置。"""
        if model is None:
            return pref_config

        result = dict(pref_config)
        current = result.get("primary")
        primary = dict(current) if isinstance(current, dict) else {}
        primary["model"] = model
        primary["enabled"] = True
        result["primary"] = primary
        return result

    def prompt_context(self) -> PromptContext:
        """生成当前输入区和 footer 使用的上下文。"""
        return PromptContext(
            mode=self.mode,
            model=primary_model_prompt_label(self.pref_config, self.model),
            workspace_label=self.workspace_label,
            access_label=access_mode_label(self.access_mode),
        )

    def apply_prompt_context(self, runtime: TuiRuntime) -> None:
        """把当前会话上下文同步到 TUI 运行时。"""
        runtime.set_prompt_context(self.prompt_context())

    async def refresh_for_prompt(self, mind: "Mind") -> None:
        """在等待用户输入期间刷新偏好和工作区标签。"""
        await self.refresh_preferences(mind)

        now = time.monotonic()
        if now - self.workspace_refreshed_at < WORKSPACE_LABEL_REFRESH:
            return None

        runtime_workspace_root = await fetch_runtime_workspace_root()
        if runtime_workspace_root is not None:
            mind.set_history_workspace(runtime_workspace_root)

        self.workspace_label = workspace_display_label(runtime_workspace_root)
        self.workspace_refreshed_at = now

    async def refresh_preferences(
        self,
        mind: "Mind",
        *,
        ttl_sec: float | None = None,
    ) -> dict[str, typing.Any]:
        """刷新偏好配置并保持模型字段同步。"""
        if ttl_sec is None:
            pref_config = await mind.fresh_pref_config()
        else:
            pref_config = await mind.fresh_pref_config(ttl_sec=ttl_sec)

        self.pref_config = self._apply_model_override(
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


async def preload_tui_prompt_context(mind: "Mind") -> None:
    """在 TUI 首帧前加载输入上下文和后台进程状态。"""
    runtime = require_tui_runtime(mind.frontend.runtime)

    pref_result, workspace_result, exec_result = await asyncio.gather(
        mind.fresh_pref_config(ttl_sec=0.0),
        fetch_runtime_workspace_root(),
        mind.native_coding.running_exec_sessions(),
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
        mind.set_history_workspace(runtime_workspace_root)

    runtime.set_prompt_context(PromptContext(
        mode=DEFAULT_RUN_MODE,
        model=primary_model_prompt_label(pref_config),
        workspace_label=workspace_display_label(runtime_workspace_root),
        access_label=access_mode_label(DEFAULT_ACCESS_MODE),
    ))
    runtime.set_process_status_label(exec_status_display_label(
        exec_snapshot,
        line_width=runtime.terminal_width,
    ))


if __name__ == '__main__':
    pass
