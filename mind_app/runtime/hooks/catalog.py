# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from mind_core.hook_trust import HookTrustState
from mind_core.hooks import (
    HookControlPolicy,
    HookEventName,
    HookFailurePolicy,
    HookMatcherSubject
)


class HookCatalogStaleError(ValueError):
    """表示 Hook 清单与执行控制时的配置已经不一致。"""


@dataclass(frozen=True, slots=True)
class HookCatalogEntry:
    """描述管理界面可检查的单个 Hook。"""
    key: str
    event: HookEventName
    command: str
    matcher: str
    matcher_subject: HookMatcherSubject | None
    timeout_sec: float
    on_error: HookFailurePolicy
    source_scope: str
    source_path: str | None
    enabled: bool
    trust_state: HookTrustState
    active: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class HookEventSummary:
    """汇总一个生命周期事件的安装和激活数量。"""
    event: HookEventName
    description: str
    matcher_subject: HookMatcherSubject | None
    control_policy: HookControlPolicy
    installed_count: int
    active_count: int


@dataclass(frozen=True, slots=True)
class HookCatalogSnapshot:
    """保存指定工作区的不可变 Hook 管理视图。"""
    workspace: str
    installed_count: int
    active_count: int
    events: tuple[HookEventSummary, ...] = ()
    hooks: tuple[HookCatalogEntry, ...] = ()
    trust_error: str = ""


if __name__ == '__main__':
    pass
