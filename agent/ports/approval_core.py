# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.approvals import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalDecisionSource,
    ApprovalFact,
    ApprovalGrantKey,
    ApprovalIdentity,
    ApprovalResolutionReason,
    SessionGrant,
)

__all__ = (
    "ApprovalFactStore",
    "ApprovalPresentationPort",
    "ApprovalReviewerPort",
    "SessionGrantStore",
)


class ApprovalFactStore(typing.Protocol):
    """定义审批事实持久化端口，负责首终态 CAS 和重启读取。"""

    async def record_requested(self, action: ApprovalAction) -> ApprovalFact:
        """记录或幂等返回一次 requested 事实。"""
        ...

    async def resolve(
        self,
        identity: ApprovalIdentity,
        decision: ApprovalDecision,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """以首终态规则提交决定并返回权威事实。"""
        ...

    async def abandon(
        self,
        identity: ApprovalIdentity,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """把无法继续等待的请求标记为 abandoned。"""
        ...

    async def find(self, identity: ApprovalIdentity) -> ApprovalFact | None:
        """按本地审批身份读取事实，不从前端或日志推断状态。"""
        ...


class SessionGrantStore(typing.Protocol):
    """定义 Session 内临时授权端口，关闭 Session 后授权必须丢弃。"""

    async def find(self, key: ApprovalGrantKey) -> SessionGrant | None:
        """读取精确 Session、环境、动作和指纹范围内的授权。"""
        ...

    async def remember(self, grant: SessionGrant) -> None:
        """保存已经通过校验的会话授权。"""
        ...

    async def clear(self, session_id: str) -> None:
        """清除指定 Session 的全部临时授权。"""
        ...


class ApprovalReviewerPort(typing.Protocol):
    """定义可替换 reviewer，生命周期由组合根管理且不得写入授权事实。"""

    async def review(self, action: ApprovalAction) -> ApprovalDecision:
        """对一个类型化动作返回绑定其指纹的决定。"""
        ...


class ApprovalPresentationPort(typing.Protocol):
    """定义中立审批展示端口，不能拥有策略、grant 或 Effect 状态。"""

    async def present(
        self,
        action: ApprovalAction,
    ) -> ApprovalDecision:
        """展示动作并返回已校验边界的前端决定。"""
        ...


if __name__ == '__main__':
    pass
