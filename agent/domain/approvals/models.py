# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import math
import typing
from dataclasses import dataclass


class ApprovalActionKind(enum.StrEnum):
    """列出可以进入统一审批链的动作类别。"""

    COMMAND = "command"
    PATCH = "patch"
    PERMISSION = "permission"
    MCP = "mcp"
    NETWORK = "network"


class ApprovalDecisionKind(enum.StrEnum):
    """列出审批核心能够记录的决定类别。"""

    ALLOW_ONCE = "allow_once"
    ALLOW_FOR_SESSION = "allow_for_session"
    APPLY_AMENDMENT = "apply_amendment"
    GRANT_FOR_RUN = "grant_for_run"
    GRANT_FOR_RUN_WITH_STRICT_AUTO_REVIEW = (
        "grant_for_run_with_strict_auto_review"
    )
    DECLINE = "decline"
    CANCEL = "cancel"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    ABANDONED = "abandoned"


class ApprovalDecisionSource(enum.StrEnum):
    """列出审批决定的来源。"""

    POLICY = "policy"
    HOOK = "hook"
    USER = "user"
    AUTO_REVIEW = "auto_review"


class ApprovalResolutionReason(enum.StrEnum):
    """列出审批请求结束的原因。"""

    POLICY = "policy"
    HOOK = "hook"
    USER = "user"
    AUTO_REVIEW = "auto_review"
    CALLER_CANCELLED = "caller_cancelled"
    CLOSED = "closed"
    PRESENTATION_FAILED = "presentation_failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    ABANDONED = "abandoned"


class ApprovalFactState(enum.StrEnum):
    """列出审批事实的单调状态。"""

    REQUESTED = "requested"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class AmendmentOperation(enum.StrEnum):
    """列出可持久化策略修改的操作。"""

    ALLOW = "allow"
    DENY = "deny"


class NetworkProtocol(enum.StrEnum):
    """列出受管网络动作支持的协议。"""

    HTTP = "http"
    HTTPS = "https"
    SOCKS5_TCP = "socks5_tcp"
    SOCKS5_UDP = "socks5_udp"


def _require_text(name: str, value: str) -> None:
    """校验一个身份或领域字段不是空文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_texts(name: str, values: tuple[str, ...]) -> None:
    """校验文本序列及其每一个元素。"""
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must be a tuple")
    if not values:
        raise ValueError(f"{name} must not be empty")
    for index, value in enumerate(values):
        _require_text(f"{name}[{index}]", value)


def _require_timestamp(name: str, value: float) -> None:
    """校验时间戳是有限的非负数。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")


def _require_action_components(
    identity: "ApprovalIdentity",
    execution: "ExecutionIdentity",
    fingerprint: "ActionFingerprint",
) -> None:
    """校验动作共享的身份、执行和指纹值对象。"""
    if not isinstance(identity, ApprovalIdentity):
        raise ValueError("identity must be an ApprovalIdentity")
    if not isinstance(execution, ExecutionIdentity):
        raise ValueError("execution must be an ExecutionIdentity")
    if not isinstance(fingerprint, ActionFingerprint):
        raise ValueError("fingerprint must be an ActionFingerprint")


@dataclass(frozen=True, slots=True)
class ActionFingerprint:
    """标识规范化动作内容，决定和动作必须绑定到同一指纹。"""

    value: str

    def __post_init__(self) -> None:
        """拒绝空指纹，避免决定落到未定义动作。"""
        _require_text("fingerprint", self.value)


@dataclass(frozen=True, slots=True)
class ApprovalIdentity:
    """标识一次审批请求及其本地作用域。"""

    session_id: str
    run_id: str
    approval_id: str
    action_id: str

    def __post_init__(self) -> None:
        """校验审批身份的四个稳定组成部分。"""
        for name, value in (
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("approval_id", self.approval_id),
            ("action_id", self.action_id),
        ):
            _require_text(name, value)


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """标识产生外部效果的本地执行上下文。"""

    environment_id: str
    execution_id: str
    tool_call_id: str

    def __post_init__(self) -> None:
        """校验执行归属不能依赖当前活动 Turn 猜测。"""
        for name, value in (
            ("environment_id", self.environment_id),
            ("execution_id", self.execution_id),
            ("tool_call_id", self.tool_call_id),
        ):
            _require_text(name, value)


@dataclass(frozen=True, slots=True)
class NetworkTarget:
    """保存已规范化的网络目标。"""

    host: str
    protocol: NetworkProtocol
    port: int

    def __post_init__(self) -> None:
        """校验网络目标的主机、协议和端口范围。"""
        _require_text("host", self.host)
        if not isinstance(self.protocol, NetworkProtocol):
            raise ValueError("protocol must be a NetworkProtocol")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ValueError("port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class CommandApprovalAction:
    """描述一次命令执行审批动作。"""

    identity: ApprovalIdentity
    execution: ExecutionIdentity
    fingerprint: ActionFingerprint
    command: tuple[str, ...]
    cwd: str
    justification: str | None = None
    kind: typing.ClassVar[ApprovalActionKind] = ApprovalActionKind.COMMAND

    def __post_init__(self) -> None:
        """校验命令和工作目录已经在边界完成规范化。"""
        _require_action_components(self.identity, self.execution, self.fingerprint)
        _require_texts("command", self.command)
        _require_text("cwd", self.cwd)
        if self.justification is not None:
            _require_text("justification", self.justification)


@dataclass(frozen=True, slots=True)
class PatchApprovalAction:
    """描述一次工作区补丁审批动作。"""

    identity: ApprovalIdentity
    execution: ExecutionIdentity
    fingerprint: ActionFingerprint
    files: tuple[str, ...]
    summary: str
    kind: typing.ClassVar[ApprovalActionKind] = ApprovalActionKind.PATCH

    def __post_init__(self) -> None:
        """校验补丁文件列表和展示摘要。"""
        _require_action_components(self.identity, self.execution, self.fingerprint)
        _require_texts("files", self.files)
        _require_text("summary", self.summary)


@dataclass(frozen=True, slots=True)
class PermissionApprovalAction:
    """描述一次额外权限审批动作。"""

    identity: ApprovalIdentity
    execution: ExecutionIdentity
    fingerprint: ActionFingerprint
    permission_scope: str
    reason: str
    kind: typing.ClassVar[ApprovalActionKind] = ApprovalActionKind.PERMISSION

    def __post_init__(self) -> None:
        """校验权限范围和申请理由。"""
        _require_action_components(self.identity, self.execution, self.fingerprint)
        _require_text("permission_scope", self.permission_scope)
        _require_text("reason", self.reason)


@dataclass(frozen=True, slots=True)
class McpApprovalAction:
    """描述一次 MCP 工具审批动作。"""

    identity: ApprovalIdentity
    execution: ExecutionIdentity
    fingerprint: ActionFingerprint
    server: str
    tool_name: str
    arguments_fingerprint: ActionFingerprint
    kind: typing.ClassVar[ApprovalActionKind] = ApprovalActionKind.MCP

    def __post_init__(self) -> None:
        """校验 MCP 服务器、工具和参数指纹。"""
        _require_action_components(self.identity, self.execution, self.fingerprint)
        _require_text("server", self.server)
        _require_text("tool_name", self.tool_name)
        if not isinstance(self.arguments_fingerprint, ActionFingerprint):
            raise ValueError("arguments_fingerprint must be an ActionFingerprint")


@dataclass(frozen=True, slots=True)
class NetworkApprovalAction:
    """描述一次被网络策略阻断的审批动作。"""

    identity: ApprovalIdentity
    execution: ExecutionIdentity
    fingerprint: ActionFingerprint
    target: NetworkTarget
    reason: str
    kind: typing.ClassVar[ApprovalActionKind] = ApprovalActionKind.NETWORK

    def __post_init__(self) -> None:
        """校验网络阻断理由。"""
        _require_action_components(self.identity, self.execution, self.fingerprint)
        _require_text("reason", self.reason)


ApprovalAction: typing.TypeAlias = (
    CommandApprovalAction
    | PatchApprovalAction
    | PermissionApprovalAction
    | McpApprovalAction
    | NetworkApprovalAction
)


@dataclass(frozen=True, slots=True)
class ApprovalAmendment:
    """保存绑定到原始动作指纹的持久策略修改。"""

    operation: AmendmentOperation
    action_fingerprint: ActionFingerprint

    def __post_init__(self) -> None:
        """校验 amendment 操作和值对象。"""
        if not isinstance(self.operation, AmendmentOperation):
            raise ValueError("operation must be an AmendmentOperation")
        if not isinstance(self.action_fingerprint, ActionFingerprint):
            raise ValueError("action_fingerprint must be an ActionFingerprint")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """保存一个不可变且绑定动作指纹的审批决定。"""

    kind: ApprovalDecisionKind
    action_fingerprint: ActionFingerprint
    amendment: ApprovalAmendment | None = None

    def __post_init__(self) -> None:
        """确保 amendment 只随持久策略决定出现。"""
        if not isinstance(self.kind, ApprovalDecisionKind):
            raise ValueError("kind must be an ApprovalDecisionKind")
        if not isinstance(self.action_fingerprint, ActionFingerprint):
            raise ValueError("action_fingerprint must be an ActionFingerprint")
        if self.kind == ApprovalDecisionKind.APPLY_AMENDMENT:
            if self.amendment is None:
                raise ValueError("apply_amendment requires an amendment")
        elif self.amendment is not None:
            raise ValueError("amendment is only valid for apply_amendment")


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """保存审批决定、来源、收束原因和事实版本。"""

    decision: ApprovalDecision
    source: ApprovalDecisionSource
    reason: ApprovalResolutionReason
    fact_version: int
    resolved_at: float

    def __post_init__(self) -> None:
        """校验终态事实版本和时间。"""
        if not isinstance(self.decision, ApprovalDecision):
            raise ValueError("decision must be an ApprovalDecision")
        if not isinstance(self.source, ApprovalDecisionSource):
            raise ValueError("source must be an ApprovalDecisionSource")
        if not isinstance(self.reason, ApprovalResolutionReason):
            raise ValueError("reason must be an ApprovalResolutionReason")
        if isinstance(self.fact_version, bool) or not isinstance(self.fact_version, int):
            raise ValueError("fact_version must be an integer")
        if self.fact_version < 1:
            raise ValueError("fact_version must be positive")
        _require_timestamp("resolved_at", self.resolved_at)


@dataclass(frozen=True, slots=True)
class ApprovalFact:
    """保存一次审批从 requested 到首个终态的单调事实。"""

    identity: ApprovalIdentity
    action_kind: ApprovalActionKind
    action_fingerprint: ActionFingerprint
    state: ApprovalFactState
    version: int
    outcome: ApprovalOutcome | None = None

    def __post_init__(self) -> None:
        """校验事实状态、版本和终态载荷的一致性。"""
        if not isinstance(self.identity, ApprovalIdentity):
            raise ValueError("identity must be an ApprovalIdentity")
        if not isinstance(self.action_kind, ApprovalActionKind):
            raise ValueError("action_kind must be an ApprovalActionKind")
        if not isinstance(self.action_fingerprint, ActionFingerprint):
            raise ValueError("action_fingerprint must be an ActionFingerprint")
        if not isinstance(self.state, ApprovalFactState):
            raise ValueError("state must be an ApprovalFactState")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ValueError("version must be an integer")
        if self.version < 0:
            raise ValueError("version must not be negative")
        if self.state is ApprovalFactState.REQUESTED:
            if self.version != 0 or self.outcome is not None:
                raise ValueError("requested fact must have version zero and no outcome")
        elif self.outcome is None or self.version < 1:
            raise ValueError("terminal fact must have a positive version and outcome")
        elif self.outcome.fact_version != self.version:
            raise ValueError("outcome version must match fact version")
        elif self.outcome.decision.action_fingerprint != self.action_fingerprint:
            raise ValueError("outcome does not match action fingerprint")

    @classmethod
    def requested(cls, action: ApprovalAction) -> "ApprovalFact":
        """为动作创建尚未决定的审批事实。"""
        return cls(
            identity=action.identity,
            action_kind=action.kind,
            action_fingerprint=action.fingerprint,
            state=ApprovalFactState.REQUESTED,
            version=0,
        )

    def resolve(
        self,
        decision: ApprovalDecision,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> "ApprovalFact":
        """把 requested 事实推进到首个 resolved 终态。"""
        from .rules import validate_decision_kind

        self._require_requested()
        self._require_matching_decision(decision)
        validate_decision_kind(self.action_kind, decision.kind)
        if decision.kind is ApprovalDecisionKind.ABANDONED:
            raise ValueError("use abandon for an abandoned approval fact")
        next_version = self.version + 1
        return ApprovalFact(
            identity=self.identity,
            action_kind=self.action_kind,
            action_fingerprint=self.action_fingerprint,
            state=ApprovalFactState.RESOLVED,
            version=next_version,
            outcome=ApprovalOutcome(
                decision=decision,
                source=source,
                reason=reason,
                fact_version=next_version,
                resolved_at=resolved_at,
            ),
        )

    def abandon(
        self,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason = ApprovalResolutionReason.ABANDONED,
        resolved_at: float,
    ) -> "ApprovalFact":
        """把无法继续等待的 requested 事实推进到 abandoned 终态。"""
        self._require_requested()
        next_version = self.version + 1
        decision = ApprovalDecision(
            kind=ApprovalDecisionKind.ABANDONED,
            action_fingerprint=self.action_fingerprint,
        )
        return ApprovalFact(
            identity=self.identity,
            action_kind=self.action_kind,
            action_fingerprint=self.action_fingerprint,
            state=ApprovalFactState.ABANDONED,
            version=next_version,
            outcome=ApprovalOutcome(
                decision=decision,
                source=source,
                reason=reason,
                fact_version=next_version,
                resolved_at=resolved_at,
            ),
        )

    def _require_requested(self) -> None:
        """拒绝重新打开已经终结的审批事实。"""
        if self.state is not ApprovalFactState.REQUESTED:
            raise ValueError("approval fact is already terminal")

    def _require_matching_decision(self, decision: ApprovalDecision) -> None:
        """拒绝把决定提交给不同动作。"""
        if decision.action_fingerprint != self.action_fingerprint:
            raise ValueError("approval decision does not match action fingerprint")
        if (
            decision.amendment is not None
            and decision.amendment.action_fingerprint != self.action_fingerprint
        ):
            raise ValueError("approval amendment does not match action fingerprint")


@dataclass(frozen=True, slots=True)
class ApprovalGrantKey:
    """标识一个只在当前 Session 和环境内有效的授权范围。"""

    session_id: str
    environment_id: str
    action_kind: ApprovalActionKind
    action_fingerprint: ActionFingerprint

    def __post_init__(self) -> None:
        """校验 grant 作用域不缺少本地身份。"""
        _require_text("session_id", self.session_id)
        _require_text("environment_id", self.environment_id)
        if not isinstance(self.action_kind, ApprovalActionKind):
            raise ValueError("action_kind must be an ApprovalActionKind")
        if not isinstance(self.action_fingerprint, ActionFingerprint):
            raise ValueError("action_fingerprint must be an ActionFingerprint")

    @classmethod
    def from_action(cls, action: ApprovalAction) -> "ApprovalGrantKey":
        """从动作构造精确的会话授权键。"""
        return cls(
            session_id=action.identity.session_id,
            environment_id=action.execution.environment_id,
            action_kind=action.kind,
            action_fingerprint=action.fingerprint,
        )


@dataclass(frozen=True, slots=True)
class SessionGrant:
    """保存一个只允许当前 Session 使用的授权。"""

    key: ApprovalGrantKey
    decision: ApprovalDecision
    granted_at: float

    def __post_init__(self) -> None:
        """确保会话授权只能由 allow-for-session 决定创建。"""
        if self.decision.kind is not ApprovalDecisionKind.ALLOW_FOR_SESSION:
            raise ValueError("session grant requires allow_for_session")
        if self.decision.action_fingerprint != self.key.action_fingerprint:
            raise ValueError("session grant does not match action fingerprint")
        _require_timestamp("granted_at", self.granted_at)


if __name__ == '__main__':
    pass
