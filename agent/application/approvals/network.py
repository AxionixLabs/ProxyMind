# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing
from collections.abc import (
    Awaitable,
    Callable,
)

from agent.application.approvals.models import ApprovalOutcome
from agent.domain.approvals import NetworkTarget
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.network import NetworkPolicyPort
from infrastructure.platform.network import (
    BlockedNetworkRequest,
    ManagedNetworkRule,
    NetworkDecision,
)

__all__ = ("NetworkApprovalService",)


NetworkRuleSink: typing.TypeAlias = Callable[
    [ManagedNetworkRule],
    Awaitable[None],
]


class NetworkApprovalService:
    """把代理阻断转换为通用 network_access 审批并应用授权范围。"""

    def __init__(
        self,
        coordinator: ApprovalCoordinatorPort,
        policy: NetworkPolicyPort,
        *,
        rule_sink: NetworkRuleSink | None = None,
    ) -> None:
        """绑定审批协调器、运行时策略和可选持久规则写入器。"""
        self._coordinator = coordinator
        self._policy = policy
        self._rule_sink = rule_sink

    def handler(
        self,
        *,
        session_id: str,
        run_id: str,
        environment_id: str,
        execution_id: str,
    ) -> Callable[[BlockedNetworkRequest], Awaitable[None]]:
        """创建绑定本地 Session/Run 身份的代理阻断回调。"""
        normalized_session = str(session_id or "").strip()
        normalized_run = str(run_id or "").strip()
        normalized_environment = str(environment_id or "").strip() or "default"
        normalized_execution = str(execution_id or "").strip()
        if not normalized_session or not normalized_run or not normalized_execution:
            raise ValueError("network approval handler identity is incomplete")

        async def handle(request: BlockedNetworkRequest) -> None:
            """处理一项代理阻断。"""
            await self.request(
                request,
                session_id=normalized_session,
                run_id=normalized_run,
                environment_id=normalized_environment,
                execution_id=normalized_execution,
            )

        return handle

    async def request(
        self,
        blocked: BlockedNetworkRequest,
        *,
        session_id: str,
        run_id: str,
        environment_id: str,
        execution_id: str,
    ) -> ApprovalOutcome:
        """展示 network_access 卡并按决定更新一次、Session 或持久规则。"""
        normalized_session = str(session_id or "").strip()
        normalized_run = str(run_id or "").strip()
        normalized_environment = str(environment_id or "").strip() or "default"
        normalized_execution = str(execution_id or "").strip()
        if not normalized_session or not normalized_run or not normalized_execution:
            raise ValueError("network approval identity is incomplete")

        target = blocked.target
        approval_id = _approval_id(
            session_id=normalized_session,
            run_id=normalized_run,
            execution_id=normalized_execution,
            target=target,
        )
        target_url = f"{target.protocol.value}://{target.host}:{target.port}"
        approval = {
            "id": approval_id,
            "approval_id": approval_id,
            "request_id": approval_id,
            "action_id": approval_id,
            "call_id": approval_id,
            "session_id": normalized_session,
            "run_id": normalized_run,
            "execution_id": normalized_execution,
            "turn_id": normalized_run,
            "environment_id": normalized_environment,
            "kind": "network_access",
            "tool": "exec_command",
            "target": target_url,
            "host": target.host,
            "protocol": target.protocol.value,
            "port": target.port,
            "reason": blocked.reason,
            "available_decisions": [
                "accept",
                "acceptForSession",
                "applyNetworkPolicyAmendment",
                "decline",
            ],
            "proposed_network_policy_amendment": {
                "host": target.host,
                "protocol": target.protocol.value,
                "port": target.port,
                "action": "allow",
            },
        }
        outcome = await self._coordinator.request_outcome(approval)
        if outcome.decision == "accept":
            self._policy.grant_once(target)
        elif outcome.decision == "acceptForSession":
            self._policy.grant_for_session(target, normalized_session)
        elif outcome.decision == "applyNetworkPolicyAmendment":
            rule = ManagedNetworkRule(
                host=target.host,
                protocol=target.protocol,
                decision=NetworkDecision.ALLOW,
                port=target.port,
            )
            sink = self._rule_sink
            if sink is not None:
                await sink(rule)
            self._policy.install_rule(rule)
        return outcome


def _approval_id(
    *,
    session_id: str,
    run_id: str,
    execution_id: str,
    target: NetworkTarget,
) -> str:
    """生成同一 Run/目标可复用的稳定审批 ID。"""
    encoded = json.dumps(
        {
            "session_id": session_id,
            "run_id": run_id,
            "execution_id": execution_id,
            "host": target.host.casefold().rstrip("."),
            "protocol": target.protocol.value,
            "port": target.port,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "network-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


if __name__ == '__main__':
    pass
