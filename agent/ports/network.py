# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.approvals import NetworkTarget

__all__ = (
    "NetworkBlockedHandler",
    "NetworkBlockedRequest",
    "NetworkBlockedHandlerFactory",
    "NetworkRulePort",
    "NetworkPolicyPort",
)


class NetworkBlockedRequest(typing.Protocol):
    """定义执行层向审批用例报告的网络阻断快照。"""

    target: NetworkTarget
    reason: str


class NetworkBlockedHandler(typing.Protocol):
    """定义处理单次网络阻断的异步回调。"""

    async def __call__(self, request: NetworkBlockedRequest) -> None:
        """等待审批决定并更新对应网络授权。"""
        ...


class NetworkBlockedHandlerFactory(typing.Protocol):
    """定义按 Session、Run、Environment 和 Execution 创建网络阻断回调的工厂。"""

    def __call__(
        self,
        session_id: str,
        run_id: str,
        environment_id: str,
        execution_id: str,
    ) -> NetworkBlockedHandler | None:
        """返回绑定执行身份的回调，身份不完整时由实现拒绝创建。"""
        ...


class NetworkRulePort(typing.Protocol):
    """定义可写入运行时策略的网络规则字段。"""

    host: str
    protocol: str
    port: int | None


class NetworkPolicyPort(typing.Protocol):
    """定义受管代理与审批用例共享的运行时网络策略。"""

    def decide(self, target: NetworkTarget, *, session_id: str = "") -> str:
        """评估目标当前是否允许连接。"""
        ...

    def grant_once(self, target: NetworkTarget) -> None:
        """授予目标一次连接。"""
        ...

    def grant_for_session(self, target: NetworkTarget, session_id: str) -> None:
        """授予目标在指定 Session 内连接。"""
        ...

    def install_rule(self, rule: NetworkRulePort) -> None:
        """安装已确认的持久运行时规则。"""
        ...


if __name__ == '__main__':
    pass
