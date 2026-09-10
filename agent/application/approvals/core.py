# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass

from agent.domain.approvals import (
    ApprovalAction,
    ApprovalActionKind,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalFact,
    ApprovalFactState,
    ApprovalResolutionReason,
    ApprovalReviewConflict,
    SessionGrant,
    approval_grant_key,
    validate_decision,
)
from agent.ports.approval_core import (
    ApprovalFactStore,
    ApprovalPresentationPort,
    ApprovalReviewerPort,
    SessionGrantStore,
)
from agent.ports.persistence import (
    EffectIntent,
    EffectJournal,
    LocalEffectReconciliationRequired,
)

__all__ = (
    "ApprovalCore",
    "ApprovalExecutionResult",
    "ApprovalPolicyEvaluator",
    "PresentationArbiter",
    "ReviewResult",
    "ReviewerBinding",
    "ReviewerChain",
    "RunApprovalRegistry",
)


class ApprovalPolicyEvaluator:
    """按显式动作类别映射纯策略决定，不读取环境或前端状态。"""

    def __init__(
        self,
        decisions: Mapping[ApprovalActionKind, ApprovalDecisionKind] | None = None,
    ) -> None:
        """绑定不可变的动作类别到决定映射。"""
        self._decisions = dict(decisions or {})

    def evaluate(self, action: ApprovalAction) -> ApprovalDecision | None:
        """返回策略可以直接确定的决定，未命中时返回 None。"""
        decision_kind = self._decisions.get(action.kind)
        if decision_kind is None:
            return None
        decision = ApprovalDecision(
            kind=decision_kind,
            action_fingerprint=action.fingerprint,
        )
        validate_decision(action, decision)
        return decision


@dataclass(frozen=True, slots=True)
class ReviewerBinding:
    """把 reviewer 实现和其决定来源绑定。"""

    source: ApprovalDecisionSource
    reviewer: ApprovalReviewerPort


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """保存 reviewer 返回的决定及其来源。"""

    decision: ApprovalDecision
    source: ApprovalDecisionSource
    reason: ApprovalResolutionReason


class ReviewerChain:
    """按优先级串行调用 reviewer，并在异常时安全拒绝。"""

    def __init__(self, reviewers: Sequence[ReviewerBinding] = ()) -> None:
        """绑定 Hook、auto review 或其他 reviewer 顺序。"""
        self._reviewers = tuple(reviewers)

    async def review(self, action: ApprovalAction) -> ReviewResult | None:
        """返回第一个适用 reviewer 的决定。"""
        for binding in self._reviewers:
            try:
                decision = await binding.reviewer.review(action)
            except asyncio.CancelledError:
                raise
            except ApprovalReviewConflict:
                raise
            except Exception:
                return ReviewResult(
                    decision=ApprovalDecision(
                        kind=ApprovalDecisionKind.UNAVAILABLE,
                        action_fingerprint=action.fingerprint,
                    ),
                    source=binding.source,
                    reason=ApprovalResolutionReason.UNAVAILABLE,
                )
            if decision is None:
                continue
            validate_decision(action, decision)
            return ReviewResult(
                decision=decision,
                source=binding.source,
                reason=_resolution_reason(binding.source),
            )
        return None


class PresentationArbiter:
    """串行化审批展示，展示锁与事实决定等待相互独立。"""

    def __init__(self, presentation: ApprovalPresentationPort) -> None:
        """绑定只负责 UI 或交互的展示端口。"""
        self._presentation = presentation
        self._lock = asyncio.Lock()

    async def present(self, action: ApprovalAction) -> ApprovalDecision:
        """按 FIFO 锁顺序展示一项动作并等待决定。"""
        async with self._lock:
            decision = await self._presentation.present(action)
        validate_decision(action, decision)
        return decision


@dataclass(slots=True)
class _RegisteredApproval:
    """保存一个身份对应的共享请求任务。"""

    action_kind: ApprovalActionKind
    action_fingerprint: str
    task: asyncio.Task[ApprovalFact]


@dataclass(frozen=True, slots=True)
class _ApprovalRegistryKey:
    """标识运行期应共享的同一审批等待任务。"""

    session_id: str
    run_id: str
    approval_id: str
    action_id: str


def _approval_registry_key(action: ApprovalAction) -> _ApprovalRegistryKey:
    """让同一 Run 内完全相同的 MCP 动作共享一个 pending 请求。"""
    identity = action.identity
    if action.kind is ApprovalActionKind.MCP:
        return _ApprovalRegistryKey(
            session_id=identity.session_id,
            run_id=identity.run_id,
            approval_id=f"mcp:{action.fingerprint.value}",
            action_id="shared",
        )
    return _ApprovalRegistryKey(
        session_id=identity.session_id,
        run_id=identity.run_id,
        approval_id=identity.approval_id,
        action_id=identity.action_id,
    )


class RunApprovalRegistry:
    """按完整 Session/Run 身份去重审批任务并隔离不同 Run。"""

    def __init__(self) -> None:
        """创建空的运行期审批注册表。"""
        self._entries: dict[_ApprovalRegistryKey, _RegisteredApproval] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        action: ApprovalAction,
        operation: Callable[[], Awaitable[ApprovalFact]],
    ) -> ApprovalFact:
        """共享同一身份的进行中任务，并拒绝语义冲突。"""
        identity = action.identity
        registry_key = _approval_registry_key(action)
        async with self._lock:
            current = self._entries.get(registry_key)
            if current is not None:
                if (
                    current.action_kind is not action.kind
                    or current.action_fingerprint != action.fingerprint.value
                ):
                    raise ValueError("approval identity conflicts with active action")
                task = current.task
            else:
                task = asyncio.create_task(
                    operation(),
                    name=f"approval {identity.approval_id}",
                )
                self._entries[registry_key] = _RegisteredApproval(
                    action_kind=action.kind,
                    action_fingerprint=action.fingerprint.value,
                    task=task,
                )

        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    current = self._entries.get(registry_key)
                    if current is not None and current.task is task:
                        del self._entries[registry_key]

    async def close(self) -> None:
        """取消全部未完成审批任务并清空注册表。"""
        async with self._lock:
            tasks = tuple(entry.task for entry in self._entries.values())
            self._entries.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class ApprovalExecutionResult:
    """保存审批事实和外部 Effect 的确定结果。"""

    fact: ApprovalFact
    result_payload: dict[str, object] | None
    reused: bool = False


class ApprovalCore:
    """协调策略、reviewer、展示、事实、grant 和外部 Effect。"""

    def __init__(
        self,
        fact_store: ApprovalFactStore,
        grant_store: SessionGrantStore,
        *,
        policy: ApprovalPolicyEvaluator | None = None,
        reviewer_chain: ReviewerChain | None = None,
        presentation: PresentationArbiter | None = None,
        registry: RunApprovalRegistry | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定审批事实、授权、策略和交互端口。"""
        self._facts = fact_store
        self._grants = grant_store
        self._policy = policy or ApprovalPolicyEvaluator()
        self._reviewers = reviewer_chain or ReviewerChain()
        self._presentation = presentation
        self._registry = registry or RunApprovalRegistry()
        self._clock = clock

    async def request(self, action: ApprovalAction) -> ApprovalFact:
        """执行一次可恢复、可去重的审批请求。"""
        return await self._registry.run(
            action,
            lambda: self._request_once(action),
        )

    async def close(self) -> None:
        """关闭运行期审批任务。"""
        await self._registry.close()

    async def execute_effect(
        self,
        action: ApprovalAction,
        effect: EffectIntent,
        execute: Callable[[], Awaitable[dict[str, object]]],
        journal: EffectJournal,
    ) -> ApprovalExecutionResult:
        """只有审批获准后才取得 Effect 执行权并提交结果。"""
        fact = await self.request(action)
        if not _allows_effect(fact):
            return ApprovalExecutionResult(fact, None)

        journal_decision = await journal.begin(effect)
        if journal_decision.action == "reuse":
            payload = journal_decision.result_payload
            if payload is None:
                raise ValueError("reused effect is missing result payload")
            return ApprovalExecutionResult(fact, payload, reused=True)
        if journal_decision.action == "reconcile":
            raise LocalEffectReconciliationRequired(effect.effect_id)

        try:
            payload = await execute()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            await journal.mark_unknown(effect, error)
            raise
        await journal.commit(effect, payload)
        return ApprovalExecutionResult(fact, payload)

    async def _request_once(self, action: ApprovalAction) -> ApprovalFact:
        """处理一次已经按身份去重的审批请求。"""
        current = await self._facts.find(action.identity)
        if current is not None:
            if (
                current.action_kind is not action.kind
                or current.action_fingerprint != action.fingerprint
            ):
                raise ValueError("approval identity conflicts with persisted action")
            if current.state is not ApprovalFactState.REQUESTED:
                return current

        current = await self._facts.record_requested(action)
        if current.state is not ApprovalFactState.REQUESTED:
            return current

        grant = await self._grants.find(approval_grant_key(action))
        if grant is not None:
            return await self._resolve(
                action,
                current,
                ApprovalDecision(
                    kind=ApprovalDecisionKind.ALLOW_FOR_SESSION,
                    action_fingerprint=action.fingerprint,
                ),
                source=ApprovalDecisionSource.POLICY,
                reason=ApprovalResolutionReason.POLICY,
            )

        policy_decision = self._policy.evaluate(action)
        if policy_decision is not None:
            return await self._resolve(
                action,
                current,
                policy_decision,
                source=ApprovalDecisionSource.POLICY,
                reason=ApprovalResolutionReason.POLICY,
            )

        review = await self._reviewers.review(action)
        if review is not None:
            return await self._resolve(
                action,
                current,
                review.decision,
                source=review.source,
                reason=review.reason,
            )

        presentation = self._presentation
        if presentation is None:
            return await self._facts.resolve(
                current.identity,
                ApprovalDecision(
                    kind=ApprovalDecisionKind.UNAVAILABLE,
                    action_fingerprint=action.fingerprint,
                ),
                source=ApprovalDecisionSource.POLICY,
                reason=ApprovalResolutionReason.UNAVAILABLE,
                resolved_at=self._clock(),
            )

        try:
            decision = await presentation.present(action)
        except asyncio.CancelledError:
            return await self._facts.abandon(
                current.identity,
                source=ApprovalDecisionSource.POLICY,
                reason=ApprovalResolutionReason.CLOSED,
                resolved_at=self._clock(),
            )
        return await self._resolve(
            action,
            current,
            decision,
            source=ApprovalDecisionSource.USER,
            reason=ApprovalResolutionReason.USER,
        )

    async def _resolve(
        self,
        action: ApprovalAction,
        current: ApprovalFact,
        decision: ApprovalDecision,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
    ) -> ApprovalFact:
        """校验并提交决定，成功的 Session grant 才进入 grant store。"""
        validate_decision(action, decision)
        resolved = await self._facts.resolve(
            current.identity,
            decision,
            source=source,
            reason=reason,
            resolved_at=self._clock(),
        )
        remember_for_session = (
            decision.kind is ApprovalDecisionKind.ALLOW_FOR_SESSION
            or (
                action.kind is ApprovalActionKind.MCP
                and decision.kind is ApprovalDecisionKind.APPLY_AMENDMENT
            )
        )
        if remember_for_session:
            grant_decision = ApprovalDecision(
                kind=ApprovalDecisionKind.ALLOW_FOR_SESSION,
                action_fingerprint=action.fingerprint,
            )
            await self._grants.remember(
                SessionGrant(
                    key=approval_grant_key(action),
                    decision=grant_decision,
                    granted_at=self._clock(),
                )
            )
        return resolved

def _allows_effect(fact: ApprovalFact) -> bool:
    """判断事实是否允许继续取得外部效果执行权。"""
    outcome = fact.outcome
    return outcome is not None and outcome.decision.kind in {
        ApprovalDecisionKind.ALLOW_ONCE,
        ApprovalDecisionKind.ALLOW_FOR_SESSION,
        ApprovalDecisionKind.APPLY_AMENDMENT,
        ApprovalDecisionKind.GRANT_FOR_RUN,
        ApprovalDecisionKind.GRANT_FOR_RUN_WITH_STRICT_AUTO_REVIEW,
    }


def _resolution_reason(
    source: ApprovalDecisionSource,
) -> ApprovalResolutionReason:
    """将 reviewer 来源转换为终态收束原因。"""
    return ApprovalResolutionReason(source.value)


if __name__ == '__main__':
    pass
