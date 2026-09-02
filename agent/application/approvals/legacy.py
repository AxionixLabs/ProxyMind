# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import hashlib
import json
import typing
from collections.abc import Mapping
from urllib.parse import urlparse

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.models import (
    ApprovalDecisionSource as LegacyDecisionSource,
    ApprovalOutcome as LegacyOutcome,
    ApprovalRequest,
    ApprovalRequestKey,
    ApprovalResolutionReason as LegacyResolutionReason,
)
from agent.application.approvals.factory import build_approval_request
from agent.application.approvals.core import (
    ApprovalCore,
    PresentationArbiter,
)
from agent.domain.approvals import (
    ActionFingerprint,
    AmendmentOperation,
    ApprovalAction,
    ApprovalActionKind,
    ApprovalAmendment,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalFact,
    ApprovalFactState,
    ApprovalIdentity,
    ApprovalResolutionReason,
    CommandApprovalAction,
    ExecutionIdentity,
    McpApprovalAction,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
    NetworkApprovalAction,
    NetworkProtocol,
    NetworkTarget,
    PatchApprovalAction,
    PermissionApprovalAction,
)
from agent.ports.approval_core import (
    ApprovalFactStore,
    ApprovalPresentationPort,
    SessionGrantStore,
)

__all__ = ("DomainApprovalCoordinator", "domain_action_from_request")


class _LegacyPresentation(ApprovalPresentationPort):
    """把类型化动作委托给现有前端 coordinator 展示。"""

    def __init__(self, coordinator: ApprovalCoordinator) -> None:
        """绑定旧展示队列并创建按身份索引的请求快照。"""
        self._coordinator = coordinator
        self._payloads: dict[ApprovalIdentity, dict[str, typing.Any]] = {}
        self._outcomes: dict[ApprovalIdentity, LegacyOutcome] = {}

    def register(
        self,
        action: ApprovalAction,
        payload: dict[str, typing.Any],
    ) -> None:
        """登记动作对应的旧协议展示载荷。"""
        self._payloads[action.identity] = dict(payload)

    def unregister(self, identity: ApprovalIdentity) -> None:
        """释放已收束动作的展示载荷和旧终态缓存。"""
        self._payloads.pop(identity, None)
        self._outcomes.pop(identity, None)

    def take_outcome(self, identity: ApprovalIdentity) -> LegacyOutcome | None:
        """取出旧 coordinator 的来源和原因，保持既有行为。"""
        return self._outcomes.pop(identity, None)

    def clear(self) -> None:
        """清理展示适配器持有的临时载荷。"""
        self._payloads.clear()
        self._outcomes.clear()

    async def present(self, action: ApprovalAction) -> ApprovalDecision:
        """调用旧展示队列并转换为类型化决定。"""
        payload = self._payloads.get(action.identity)
        if payload is None:
            raise ValueError("legacy approval payload is unavailable")
        outcome = await self._coordinator.request_outcome(payload)
        self._outcomes[action.identity] = outcome
        return _domain_decision(action, outcome.decision)


class DomainApprovalCoordinator:
    """将旧审批端口接入通用事实、grant 和 reviewer 核心。"""

    def __init__(
        self,
        legacy: ApprovalCoordinator,
        *,
        fact_store: "ApprovalFactStore",
        grant_store: "SessionGrantStore",
    ) -> None:
        """绑定旧展示队列和新核心状态存储。"""
        self._legacy = legacy
        self._presentation = _LegacyPresentation(legacy)
        self._core = ApprovalCore(
            fact_store,
            grant_store,
            presentation=PresentationArbiter(self._presentation),
        )
        self._restored_tasks: set[asyncio.Task[ApprovalFact]] = set()

    @property
    def snapshot(self):
        """返回旧展示队列快照，供现有前端观察。"""
        return self._legacy.snapshot

    async def request(self, approval: Mapping[str, typing.Any]) -> str:
        """提交旧载荷并返回旧协议决定。"""
        outcome = await self.request_outcome(approval)
        return outcome.decision

    async def request_outcome(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> LegacyOutcome:
        """把旧载荷转换为类型化动作并返回兼容终态。"""
        request = (
            approval
            if isinstance(approval, ApprovalRequest)
            else build_approval_request(approval)
        )
        action = domain_action_from_request(request)
        payload = request.payload.as_dict()
        return await self.request_action_outcome(action, payload)

    async def request_action_outcome(
        self,
        action: ApprovalAction,
        presentation: Mapping[str, typing.Any],
    ) -> LegacyOutcome:
        """把类型化动作直接交给审批核心并沿用当前展示队列。"""
        payload = dict(presentation)
        self._presentation.register(action, payload)
        try:
            fact = await self._core.request(action)
            legacy_outcome = self._presentation.take_outcome(action.identity)
            if legacy_outcome is not None:
                return legacy_outcome
            return _legacy_outcome(fact)
        finally:
            self._presentation.unregister(action.identity)

    async def resolve(
        self,
        request: ApprovalRequestKey | str,
        decision: str,
        *,
        source: LegacyDecisionSource = "policy",
    ) -> bool:
        """把外部旧决定转发给展示队列，交由核心记录事实。"""
        return await self._legacy.resolve(request, decision, source=source)

    async def restore_pending(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> bool:
        """恢复旧审批并启动无调用方的核心等待任务。"""
        request = (
            approval
            if isinstance(approval, ApprovalRequest)
            else build_approval_request(approval)
        )
        action = domain_action_from_request(request)
        self._presentation.register(action, request.payload.as_dict())
        restored = await self._legacy.restore_pending(request)
        if not restored:
            self._presentation.unregister(action.identity)
            return False
        task = asyncio.create_task(
            self._core.request(action),
            name=f"restored approval {action.identity.approval_id}",
        )
        self._restored_tasks.add(task)

        def _finish(completed: asyncio.Task[ApprovalFact]) -> None:
            self._restored_tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(_finish)
        return True

    async def close(self) -> None:
        """按先核心后展示顺序关闭审批资源。"""
        await self._core.close()
        await self._legacy.close()
        self._presentation.clear()


def domain_action_from_request(request: ApprovalRequest) -> ApprovalAction:
    """将旧应用请求转换为带稳定身份和指纹的领域动作。"""
    payload = request.payload.as_dict()
    identity = _identity_from_request(request, payload)
    execution = ExecutionIdentity(
        environment_id=_text(
            payload.get("environment_id") or payload.get("environment")
        ) or "default",
        execution_id=_text(payload.get("execution_id"))
        or identity.run_id,
        tool_call_id=_text(payload.get("call_id")) or identity.action_id,
    )
    fingerprint = ActionFingerprint(_fingerprint(payload, request.key.kind))
    kind = request.key.kind
    if kind in {"command", "write_stdin"}:
        command = payload.get("command")
        if command in (None, ""):
            arguments = payload.get("arguments")
            command = arguments.get("command") if isinstance(arguments, dict) else ""
        values = (
            tuple(str(item) for item in command)
            if isinstance(command, (list, tuple))
            else (str(command or "approval"),)
        )
        if kind == "write_stdin":
            values = ("write_stdin", _text(payload.get("input")))
        return CommandApprovalAction(
            identity=identity,
            execution=execution,
            fingerprint=fingerprint,
            command=values,
            cwd=_text(payload.get("cwd")) or ".",
            justification=_optional_text(
                payload.get("justification") or payload.get("reason")
            ),
        )
    if kind == "apply_patch":
        files = payload.get("files") or payload.get("patch_scope")
        file_values = (
            tuple(str(item) for item in files)
            if isinstance(files, (list, tuple)) and files
            else ("workspace",)
        )
        return PatchApprovalAction(
            identity=identity,
            execution=execution,
            fingerprint=fingerprint,
            files=file_values,
            summary=_text(payload.get("patch")) or "workspace patch",
        )
    if kind == "request_permissions":
        permissions = payload.get("permissions")
        scope = json.dumps(
            permissions if isinstance(permissions, dict) else {},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return PermissionApprovalAction(
            identity=identity,
            execution=execution,
            fingerprint=fingerprint,
            permission_scope=scope,
            reason=_text(payload.get("reason") or payload.get("justification"))
            or "permission request",
        )
    if kind == "mcp_tool_call":
        arguments = payload.get("arguments")
        arguments_json = json.dumps(
            arguments if isinstance(arguments, dict) else {},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return McpApprovalAction(
            identity=identity,
            execution=execution,
            fingerprint=fingerprint,
            descriptor=McpToolDescriptor(
                server=_text(payload.get("server")) or "mcp",
                exposed_name=_text(payload.get("tool")) or "mcp_tool_call",
                tool_name=_text(payload.get("tool_name")) or request.key.tool,
                schema_fingerprint=ActionFingerprint(
                    _text(payload.get("schema_fingerprint")) or fingerprint.value
                ),
                annotations=_mcp_annotations(payload.get("annotations")),
                policy=McpApprovalPolicy(McpApprovalMode.PROMPT),
                title=_optional_text(payload.get("tool_title")),
                description=_optional_text(payload.get("tool_description")),
                connector_id=_optional_text(payload.get("connector_id")),
                connector_name=_optional_text(payload.get("connector_name")),
                connector_description=_optional_text(
                    payload.get("connector_description")
                ),
                connected_account=_optional_text(
                    payload.get("connected_account_email")
                ),
                transport=_text(payload.get("transport")) or "external",
            ),
            arguments_fingerprint=ActionFingerprint(
                hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()
            ),
        )
    return _network_action(identity, execution, fingerprint, payload)


def _network_action(
    identity: ApprovalIdentity,
    execution: ExecutionIdentity,
    fingerprint: ActionFingerprint,
    payload: dict[str, typing.Any],
) -> NetworkApprovalAction:
    """从旧 network_access 载荷构造规范化网络动作。"""
    target = _text(payload.get("target"))
    parsed = urlparse(target)
    host = _text(payload.get("host")) or _text(parsed.hostname) or target or "unknown"
    raw_protocol = (
        _text(payload.get("protocol")) or _text(parsed.scheme) or "https"
    ).casefold()
    protocol = {
        "http": NetworkProtocol.HTTP,
        "https": NetworkProtocol.HTTPS,
        "socks5": NetworkProtocol.SOCKS5_TCP,
        "socks5_tcp": NetworkProtocol.SOCKS5_TCP,
        "socks5_udp": NetworkProtocol.SOCKS5_UDP,
    }.get(raw_protocol, NetworkProtocol.HTTPS)
    raw_port = payload.get("port")
    if isinstance(raw_port, bool) or not isinstance(raw_port, int):
        try:
            raw_port = parsed.port
        except ValueError:
            raw_port = None
    port = raw_port or {
        NetworkProtocol.HTTP: 80,
        NetworkProtocol.HTTPS: 443,
        NetworkProtocol.SOCKS5_TCP: 1080,
        NetworkProtocol.SOCKS5_UDP: 1080,
    }[protocol]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        port = 443
    return NetworkApprovalAction(
        identity=identity,
        execution=execution,
        fingerprint=fingerprint,
        target=NetworkTarget(host=host, protocol=protocol, port=port),
        reason=_text(payload.get("reason")) or "network policy request",
    )


def _identity_from_request(
    request: ApprovalRequest,
    payload: dict[str, typing.Any],
) -> ApprovalIdentity:
    """构造不依赖当前活动 Turn 的本地审批身份。"""
    request_id = request.key.request_id
    return ApprovalIdentity(
        session_id=_text(
            payload.get("session_id")
            or payload.get("sid")
            or payload.get("conversation_id")
        ) or "legacy-session",
        run_id=_text(
            payload.get("run_id") or payload.get("turn_id")
        ) or "legacy-run",
        approval_id=_text(payload.get("approval_id")) or request_id,
        action_id=_text(payload.get("action_id") or payload.get("call_id"))
        or request_id,
    )


def _fingerprint(payload: dict[str, typing.Any], kind: str) -> str:
    """为旧动态载荷生成稳定的动作 SHA-256 指纹。"""
    fields = {
        "kind": kind,
        "command": payload.get("command"),
        "cwd": payload.get("cwd"),
        "patch": payload.get("patch"),
        "files": payload.get("files") or payload.get("patch_scope"),
        "permissions": payload.get("permissions"),
        "server": payload.get("server"),
        "tool_name": payload.get("tool_name"),
        "arguments": payload.get("arguments"),
        "host": payload.get("host"),
        "protocol": payload.get("protocol"),
        "port": payload.get("port"),
    }
    encoded = json.dumps(
        fields,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _domain_decision(
    action: ApprovalAction,
    value: str,
) -> ApprovalDecision:
    """将旧协议决定转换为领域决定。"""
    mapping = {
        "accept": ApprovalDecisionKind.ALLOW_ONCE,
        "acceptForSession": ApprovalDecisionKind.ALLOW_FOR_SESSION,
        "acceptWithExecpolicyAmendment": ApprovalDecisionKind.APPLY_AMENDMENT,
        "applyNetworkPolicyAmendment": ApprovalDecisionKind.APPLY_AMENDMENT,
        "grantForTurn": ApprovalDecisionKind.GRANT_FOR_RUN,
        "grantForTurnWithStrictAutoReview": (
            ApprovalDecisionKind.GRANT_FOR_RUN_WITH_STRICT_AUTO_REVIEW
        ),
        "grantForSession": ApprovalDecisionKind.ALLOW_FOR_SESSION,
        "decline": ApprovalDecisionKind.DECLINE,
        "cancel": ApprovalDecisionKind.CANCEL,
    }
    try:
        kind = mapping[value]
    except KeyError as error:
        raise ValueError(f"unsupported approval decision: {value}") from error
    amendment = None
    if kind is ApprovalDecisionKind.APPLY_AMENDMENT:
        amendment = ApprovalAmendment(
            operation=AmendmentOperation.ALLOW,
            action_fingerprint=action.fingerprint,
        )
    decision = ApprovalDecision(
        kind=kind,
        action_fingerprint=action.fingerprint,
        amendment=amendment,
    )
    if action.kind is ApprovalActionKind.PERMISSION and value == "acceptForSession":
        raise ValueError("acceptForSession is invalid for permission approval")
    return decision


def _legacy_outcome(fact: ApprovalFact) -> LegacyOutcome:
    """把新事实转换为旧调用方可消费的决定。"""
    outcome = fact.outcome
    if outcome is None:
        raise ValueError("approval request returned without terminal outcome")
    kind = outcome.decision.kind
    if kind is ApprovalDecisionKind.ALLOW_ONCE:
        decision = "accept"
    elif kind is ApprovalDecisionKind.ALLOW_FOR_SESSION:
        decision = (
            "grantForSession"
            if fact.action_kind is ApprovalActionKind.PERMISSION
            else "acceptForSession"
        )
    elif kind is ApprovalDecisionKind.APPLY_AMENDMENT:
        decision = (
            "applyNetworkPolicyAmendment"
            if fact.action_kind is ApprovalActionKind.NETWORK
            else "acceptWithExecpolicyAmendment"
        )
    elif kind is ApprovalDecisionKind.GRANT_FOR_RUN:
        decision = "grantForTurn"
    elif kind is ApprovalDecisionKind.GRANT_FOR_RUN_WITH_STRICT_AUTO_REVIEW:
        decision = "grantForTurnWithStrictAutoReview"
    elif kind is ApprovalDecisionKind.CANCEL:
        decision = "cancel"
    else:
        decision = "decline"
    if outcome.source is ApprovalDecisionSource.POLICY:
        source: LegacyDecisionSource = "policy"
    elif outcome.source is ApprovalDecisionSource.USER:
        source = "user"
    else:
        source = "auto_review"
    if outcome.reason is ApprovalResolutionReason.POLICY:
        reason: LegacyResolutionReason = "policy"
    elif outcome.reason is ApprovalResolutionReason.USER:
        reason = "user"
    elif outcome.reason is ApprovalResolutionReason.CALLER_CANCELLED:
        reason = "caller_cancelled"
    elif outcome.reason is ApprovalResolutionReason.CLOSED:
        reason = "closed"
    elif outcome.reason is ApprovalResolutionReason.PRESENTATION_FAILED:
        reason = "presentation_failed"
    else:
        reason = "external"
    return LegacyOutcome(
        decision=decision,
        source=source,
        reason=reason,
        resolved_at=outcome.resolved_at,
    )


def _text(value: object) -> str:
    """把可选载荷字段规范化为非空文本。"""
    return value.strip() if isinstance(value, str) else str(value or "").strip()


def _optional_text(value: object) -> str | None:
    """把空理由规范化为真正的 None。"""
    text = _text(value)
    return text or None


def _mcp_annotations(value: object) -> McpToolAnnotations:
    """把旧 MCP 展示载荷中的注解转换为严格领域值。"""
    fields = value if isinstance(value, dict) else {}

    def optional_bool(name: str) -> bool | None:
        raw = fields.get(name)
        return raw if isinstance(raw, bool) else None

    return McpToolAnnotations(
        read_only_hint=optional_bool("read_only_hint"),
        destructive_hint=optional_bool("destructive_hint"),
        open_world_hint=optional_bool("open_world_hint"),
    )


if __name__ == '__main__':
    pass
