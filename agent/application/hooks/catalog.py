# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

from agent.domain.hook_trust import (
    HookTrustState,
    hook_is_active,
    hook_is_toggleable,
    hook_is_trusted,
    hook_needs_review,
)
from agent.domain.hooks import (
    HookControlPolicy,
    HookEventName,
    HookMatcherSubject,
    HookTrustPolicy,
)


class HookCatalogStaleError(ValueError):
    """表示 Hook 清单与执行控制时的配置已经不一致。"""


@dataclass(frozen=True, slots=True)
class HookCatalogEntry:
    """描述管理界面可检查的单个 Hook。"""
    key: str
    event: HookEventName
    command: str | None
    command_windows: str | None
    status_message: str | None
    matcher: str
    matcher_subject: HookMatcherSubject | None
    timeout_sec: int
    run_async: bool
    additional_context_limit: int | None
    source_scope: str
    source_path: str | None
    trust_policy: HookTrustPolicy
    trust_state: HookTrustState
    enabled: bool
    active: bool
    content_hash: str
    display_order: int = 0
    handler_type: str = "command"
    mcp_server: str | None = None
    mcp_tool: str | None = None

    @property
    def stable_key(self) -> str:
        """返回用于刷新后恢复选中的稳定标识。"""
        return self.key

    @property
    def needs_review(self) -> bool:
        """返回当前 Hook 是否需要重新审核。"""
        return hook_needs_review(self.trust_state)

    @property
    def trusted(self) -> bool:
        """返回当前 Hook 是否处于已信任状态。"""
        return hook_is_trusted(self.trust_state)

    @property
    def toggleable(self) -> bool:
        """返回当前 Hook 是否允许用户切换启用状态。"""
        return hook_is_toggleable(self.trust_state, self.trust_policy)

    @property
    def computed_active(self) -> bool:
        """返回按统一状态规则计算出的运行状态。"""
        return hook_is_active(self.trust_state, self.enabled)


@dataclass(frozen=True, slots=True)
class HookEventSummary:
    """汇总一个生命周期事件的安装和激活数量。"""
    event: HookEventName
    description: str
    matcher_subject: HookMatcherSubject | None
    control_policy: HookControlPolicy
    installed_count: int
    active_count: int
    review_count: int = 0


@dataclass(frozen=True, slots=True)
class HookCatalogSnapshot:
    """保存指定工作区的不可变 Hook 管理视图。"""
    workspace: str
    installed_count: int
    active_count: int
    events: tuple[HookEventSummary, ...] = ()
    hooks: tuple[HookCatalogEntry, ...] = ()
    warnings: tuple[str, ...] = ()


if __name__ == '__main__':
    pass
