# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from protocol.schema.identifiers import (
    normalize_turn_id,
    resolve_request_id,
    stable_request_id,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.reliable import (
    get_json_reliably,
    post_json_reliably,
)
from protocol.transport.endpoints import service_endpoints
from protocol.schema.tool_approval import (
    TOOL_APPROVAL_DECISIONS_BY_KIND,
    ToolApprovalAck,
    ToolApprovalDecision,
    ToolApprovalKind,
    ToolApprovalNetworkProtocol,
    ToolApprovalPermissionScope,
    ToolApprovalSnapshotStatus,
    ToolApprovalStatus,
    ToolApprovalSnapshot,
    ToolApprovalSnapshotItem,
    ToolApprovalTurnStatus,
)

_TOOL_RESULT_STATUS_VALUES = frozenset({
    "waiting_result",
    "result_received",
    "execution_timed_out",
    "cancelled",
    "missing",
    "not_ready",
    "turn_closed",
})

_TOOL_RESULT_ENVELOPE_KEYS = frozenset({
    "ok",
    "tool",
    "source",
    "args",
    "text",
    "attachments",
    "data",
})

_REMOVED_CLOUD_SANDBOX_HANDOFF_FIELDS: typing.Final[frozenset[str]] = frozenset({
    "pending_cloud_sandbox",
    "sandbox_requests",
    "cloud_schema",
})


class ToolResultEnvelope(typing.TypedDict):
    """描述客户端一次性提交的规范工具结果。"""
    ok: bool
    tool: str
    source: str
    args: dict[str, typing.Any]
    text: str
    attachments: list[typing.Any]
    data: dict[str, typing.Any]


class _ToolResultPayload(typing.TypedDict):
    """描述工具执行结果的请求载荷。"""
    request_id: str
    cid: str
    sid: str
    call_id: str
    name: str
    ok: bool
    result: ToolResultEnvelope
    additional_context: list[str]


class _ToolApprovalPayload(typing.TypedDict):
    """描述工具审批决定的请求载荷。"""
    request_id: str
    cid: str
    sid: str
    turn_id: str
    call_id: str
    approval_id: str
    kind: ToolApprovalKind
    decision: ToolApprovalDecision
    execpolicy_amendment_id: typing.NotRequired[str]
    reason: typing.NotRequired[str]
    additional_context: typing.NotRequired[list[str]]
    target: typing.NotRequired[str]
    host: typing.NotRequired[str]
    protocol: typing.NotRequired[ToolApprovalNetworkProtocol]
    port: typing.NotRequired[int]
    network_policy_amendment: typing.NotRequired[dict[str, str]]
    scope: typing.NotRequired[str]
    permissions: typing.NotRequired[dict[str, typing.Any]]
    strict_auto_review: typing.NotRequired[bool]
    server: typing.NotRequired[str]
    tool_name: typing.NotRequired[str]
    arguments: typing.NotRequired[typing.Any]
    mcp_request_id: typing.NotRequired[str]


class ToolApprovalRequestError(Exception):
    """描述服务端拒绝或无法确认的审批决定。"""

    def __init__(self, code: str, message: str, *, status_code: int = 0) -> None:
        """保存审批请求的错误码和响应状态。"""
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ToolResultRequestError(Exception):
    """描述工具结果投递的结构化生命周期错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 0,
        retryable: bool = False,
        details: typing.Mapping[str, typing.Any] | None = None,
        trace_id: str = "",
    ) -> None:
        """保存服务端错误码、可重试标志和权威状态字段。"""
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.details = dict(details or {})
        self.trace_id = str(trace_id or "").strip()

    @property
    def tool_status(self) -> str | None:
        """返回服务端报告的工具生命周期状态。"""
        value = self.details.get("tool_status")
        return str(value) if value else None

    @property
    def call_id(self) -> str | None:
        """返回服务端错误关联的工具调用标识。"""
        value = self.details.get("call_id")
        return str(value) if value else None

    @property
    def is_deterministic_terminal(self) -> bool:
        """返回错误是否已经确定工具调用不能再接收结果。"""
        return self.code in {
            "tool_call_already_completed",
            "tool_call_execution_timed_out",
            "tool_call_cancelled",
            "tool_call_turn_closed",
        }


def _tool_approval_kind(value: typing.Any) -> ToolApprovalKind | None:
    """把已校验的协议值收窄为审批类别。"""
    if value == "command":
        return "command"
    if value == "write_stdin":
        return "write_stdin"
    if value == "apply_patch":
        return "apply_patch"
    if value == "network_access":
        return "network_access"
    if value == "request_permissions":
        return "request_permissions"
    if value == "mcp_tool_call":
        return "mcp_tool_call"
    return None


def _tool_approval_decision(value: typing.Any) -> ToolApprovalDecision | None:
    """把已校验的协议值收窄为审批决定。"""
    if value == "accept":
        return "accept"
    if value == "acceptForSession":
        return "acceptForSession"
    if value == "acceptWithExecpolicyAmendment":
        return "acceptWithExecpolicyAmendment"
    if value == "applyNetworkPolicyAmendment":
        return "applyNetworkPolicyAmendment"
    if value == "grantForTurn":
        return "grantForTurn"
    if value == "grantForTurnWithStrictAutoReview":
        return "grantForTurnWithStrictAutoReview"
    if value == "grantForSession":
        return "grantForSession"
    if value == "decline":
        return "decline"
    if value == "cancel":
        return "cancel"
    return None


def _tool_approval_status(value: typing.Any) -> ToolApprovalStatus | None:
    """把已校验的协议值收窄为工具审批状态。"""
    if value == "approved":
        return "approved"
    if value == "declined":
        return "declined"
    if value == "cancelled":
        return "cancelled"
    return None


def _tool_approval_turn_status(
    value: typing.Any,
) -> ToolApprovalTurnStatus | None:
    """把已校验的协议值收窄为轮次状态。"""
    if value == "active":
        return "active"
    if value == "interrupting":
        return "interrupting"
    return None


def _tool_approval_network_protocol(
    value: typing.Any,
) -> ToolApprovalNetworkProtocol | None:
    """把已校验的协议值收窄为网络协议。"""
    if value == "http":
        return "http"
    if value == "https":
        return "https"
    if value == "socks5_tcp":
        return "socks5_tcp"
    if value == "socks5_udp":
        return "socks5_udp"
    return None


def _tool_approval_scope(
    value: typing.Any,
) -> ToolApprovalPermissionScope | None:
    """把已校验的协议值收窄为权限生效范围。"""
    if value == "turn":
        return "turn"
    if value == "session":
        return "session"
    return None


def _tool_approval_snapshot_status(
    value: typing.Any,
) -> ToolApprovalSnapshotStatus | None:
    """把已校验的协议值收窄为审批快照状态。"""
    if value == "pending":
        return "pending"
    if value == "resolved":
        return "resolved"
    if value == "expired":
        return "expired"
    if value == "cancelled":
        return "cancelled"
    return None

class ToolApprovalSnapshotRequestError(Exception):
    """描述审批恢复快照请求失败或返回无效响应。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        """保存快照请求失败对应的 HTTP 状态码。"""
        super().__init__(message)
        self.status_code = status_code


async def reconcile_tool_approval_snapshot(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    timeout: float = 10.0,
) -> ToolApprovalSnapshot:
    """读取指定逻辑轮次的审批恢复快照。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()

    if not normalized_cid:
        raise ToolApprovalSnapshotRequestError("approval snapshot requires cid")
    if not normalized_sid:
        raise ToolApprovalSnapshotRequestError("approval snapshot requires sid")

    try:
        normalized_turn_id = normalize_turn_id(turn_id)
    except ValueError as error:
        raise ToolApprovalSnapshotRequestError(str(error)) from error

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                service_endpoints.endpoint("/turn/approval-snapshot"),
                json={
                    "cid": normalized_cid,
                    "sid": normalized_sid,
                    "turn_id": normalized_turn_id,
                },
                headers=build_service_headers(),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot request failed",
            status_code=error.response.status_code,
        ) from error
    except httpx.HTTPError as error:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot request failed"
        ) from error

    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot returned an invalid response",
            status_code=response.status_code,
        ) from error

    if not isinstance(body, dict) or body.get("ok") is not True:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot returned an invalid response",
            status_code=response.status_code,
        )

    data = body.get("data")
    if not isinstance(data, dict):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot data is invalid",
            status_code=response.status_code,
        )

    expected = {
        "cid": normalized_cid,
        "sid": normalized_sid,
        "turn_id": normalized_turn_id,
    }
    if any(
        str(data.get(key) or "").strip() != value
        for key, value in expected.items()
    ):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot identity does not match request",
            status_code=response.status_code,
        )

    raw_approvals = data.get("approvals")
    if not isinstance(raw_approvals, list):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot approvals are invalid",
            status_code=response.status_code,
        )

    approvals = tuple(
        _approval_snapshot_item(item, expected_turn_id=normalized_turn_id)
        for item in raw_approvals
    )
    turn_status = str(data.get("turn_status") or "").strip()
    if not turn_status:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot turn_status is invalid",
            status_code=response.status_code,
        )
    last_event_seq = data.get("last_event_seq")
    if (
        isinstance(last_event_seq, bool)
        or not isinstance(last_event_seq, int)
        or last_event_seq < 0
    ):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot last_event_seq is invalid",
            status_code=response.status_code,
        )
    turn_settled = data.get("turn_settled")
    if not isinstance(turn_settled, bool):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot turn_settled is invalid",
            status_code=response.status_code,
        )

    return ToolApprovalSnapshot(
        cid=normalized_cid,
        sid=normalized_sid,
        turn_id=normalized_turn_id,
        turn_status=turn_status,
        turn_settled=turn_settled,
        last_event_seq=last_event_seq,
        approvals=approvals,
    )


def _approval_snapshot_item(
    value: typing.Any,
    *,
    expected_turn_id: str
) -> ToolApprovalSnapshotItem:
    """校验并转换服务端直接返回的审批信封。"""
    if not isinstance(value, dict):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item is invalid"
        )

    def text_field(name: str, *, required: bool = False) -> str:
        raw = value.get(name)
        if raw is None:
            result = ""
        elif isinstance(raw, str):
            result = raw.strip()
        else:
            raise ToolApprovalSnapshotRequestError(
                f"approval snapshot {name} is invalid"
            )
        if required and not result:
            raise ToolApprovalSnapshotRequestError(
                f"approval snapshot {name} is invalid"
            )
        return result

    if text_field("type", required=True) != "tool.approval_required":
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item type is invalid"
        )

    approval_id = text_field("approval_id", required=True)
    turn_id     = text_field("turn_id", required=True)

    if turn_id != expected_turn_id:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item turn_id does not match request"
        )

    call_id = text_field("call_id", required=True)
    kind    = text_field("kind", required=True)

    typed_kind = _tool_approval_kind(kind)
    if typed_kind is None:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item kind is invalid"
        )

    removed_fields = {"name", "approval", "patch_scope"}
    if kind != "mcp_tool_call":
        removed_fields.add("arguments")
    present_removed = sorted(field for field in removed_fields if field in value)
    if present_removed:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot contains removed protocol fields: "
            + ", ".join(present_removed)
        )

    started_at_ms = value.get("started_at_ms")
    if (
        isinstance(started_at_ms, bool)
        or not isinstance(started_at_ms, int)
        or started_at_ms < 0
    ):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item started_at_ms is invalid"
        )

    status = text_field("status", required=True)
    typed_status = _tool_approval_snapshot_status(status)
    if typed_status is None:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item status is invalid"
        )

    raw_decisions = value.get("available_decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item available_decisions is invalid"
        )
    if (
        len(raw_decisions) > 5
        or any(
            not isinstance(item, str)
            or item not in TOOL_APPROVAL_DECISIONS_BY_KIND[typed_kind]
            for item in raw_decisions
        )
        or len(set(raw_decisions)) != len(raw_decisions)
    ):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item decisions are invalid"
        )

    if "ack" not in value:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item ack is missing"
        )
    ack = value.get("ack")
    if status == "resolved" and not isinstance(ack, dict):
        raise ToolApprovalSnapshotRequestError(
            "resolved approval snapshot item requires ack"
        )
    if status != "resolved" and ack is not None:
        raise ToolApprovalSnapshotRequestError(
            "non-resolved approval snapshot item cannot contain ack"
        )
    _validate_snapshot_action(value, kind=typed_kind, decisions=raw_decisions)
    if isinstance(ack, dict):
        _validate_snapshot_ack(
            ack,
            kind=typed_kind,
            decisions=raw_decisions,
            envelope=value,
        )

    return ToolApprovalSnapshotItem(
        approval_id=approval_id,
        turn_id=turn_id,
        call_id=call_id,
        kind=typed_kind,
        approval=dict(value),
        status=typed_status,
        ack=dict(ack) if isinstance(ack, dict) else None,
    )


def _snapshot_text(
    value: typing.Any,
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    """读取快照中的字符串字段。"""
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ToolApprovalSnapshotRequestError(
            f"approval snapshot {name} is invalid"
        )
    return value


def _snapshot_list(value: typing.Any, name: str) -> list[typing.Any]:
    """读取快照中的非空数组字段。"""
    if not isinstance(value, list) or not value:
        raise ToolApprovalSnapshotRequestError(
            f"approval snapshot {name} is invalid"
        )
    return value


def _validate_snapshot_action(
    value: dict[str, typing.Any],
    *,
    kind: str,
    decisions: list[typing.Any],
) -> None:
    """校验快照中按动作区分的必填字段。"""
    if kind == "command":
        _snapshot_text(value.get("environment_id"), "environment_id")
        command = _snapshot_list(value.get("command"), "command")
        if any(not isinstance(item, str) for item in command):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot command must contain strings"
            )
        _snapshot_text(value.get("cwd"), "cwd")
        _snapshot_text(value.get("cwd_raw"), "cwd_raw")
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
        if not isinstance(value.get("tty"), bool):
            raise ToolApprovalSnapshotRequestError("approval snapshot tty is invalid")
        if value.get("sandbox_permissions") not in {
            "use_default", "require_escalated", "with_additional_permissions"
        }:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot sandbox_permissions is invalid"
            )
        if "additional_permissions" not in value:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot additional_permissions is missing"
            )
        if value.get("sandbox_permissions") == "with_additional_permissions":
            if not isinstance(value.get("additional_permissions"), dict):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot additional_permissions are required"
                )
        elif value.get("additional_permissions") is not None:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot additional_permissions are not allowed"
            )
        if "proposed_execpolicy_amendment" not in value:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot proposed_execpolicy_amendment is missing"
            )
        amendment = value.get("proposed_execpolicy_amendment")
        has_amendment = "acceptWithExecpolicyAmendment" in decisions
        if has_amendment != isinstance(amendment, dict):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot execpolicy proposal and decision must appear together"
            )
        if isinstance(amendment, dict):
            if set(amendment) != {"command"}:
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot execpolicy proposal is invalid"
                )
            command_prefix = amendment.get("command")
            if not isinstance(command_prefix, list) or not command_prefix or any(
                not isinstance(item, str) or not item for item in command_prefix
            ):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot execpolicy proposal is invalid"
                )
        parsed_cmd = value.get("parsed_cmd")
        if not isinstance(parsed_cmd, list):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot parsed_cmd is invalid"
            )
        if any(not isinstance(item, dict) for item in parsed_cmd):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot parsed_cmd must contain objects"
            )
    elif kind == "write_stdin":
        _snapshot_text(value.get("session_id"), "session_id")
        _snapshot_text(value.get("input"), "input", allow_empty=True)
        if value.get("control") not in {"none", "interrupt", "terminate"}:
            raise ToolApprovalSnapshotRequestError("approval snapshot control is invalid")
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
    elif kind == "apply_patch":
        _snapshot_text(value.get("environment_id"), "environment_id")
        _snapshot_text(value.get("cwd"), "cwd")
        _snapshot_text(value.get("cwd_raw"), "cwd_raw")
        _snapshot_text(value.get("patch"), "patch")
        files = _snapshot_list(value.get("files"), "files")
        if any(not isinstance(item, str) or not item.strip() for item in files):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot files must contain non-empty strings"
            )
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
        if not isinstance(value.get("permissions_preapproved"), bool):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot permissions_preapproved is invalid"
            )
    elif kind == "network_access":
        _snapshot_text(value.get("environment_id"), "environment_id")
        _snapshot_text(value.get("target"), "target")
        host = _snapshot_text(value.get("host"), "host")
        if value.get("protocol") not in {"http", "https", "socks5_tcp", "socks5_udp"}:
            raise ToolApprovalSnapshotRequestError("network approval protocol is invalid")
        port = value.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ToolApprovalSnapshotRequestError("network approval port is invalid")
        command = _snapshot_list(value.get("command"), "command")
        if any(not isinstance(item, str) for item in command):
            raise ToolApprovalSnapshotRequestError(
                "network approval command must contain strings"
            )
        _snapshot_text(value.get("cwd"), "cwd")
        _snapshot_text(value.get("cwd_raw"), "cwd_raw")
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
        if "proposed_network_policy_amendment" not in value:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot proposed_network_policy_amendment is missing"
            )
        proposal = value.get("proposed_network_policy_amendment")
        has_amendment = "applyNetworkPolicyAmendment" in decisions
        if has_amendment != isinstance(proposal, dict):
            raise ToolApprovalSnapshotRequestError(
                "network policy proposal and decision must appear together"
            )
        if isinstance(proposal, dict) and (
            set(proposal) != {"host", "action"}
            or proposal.get("host") != host
            or proposal.get("action") not in {"allow", "deny"}
        ):
            raise ToolApprovalSnapshotRequestError(
                "network policy proposal does not match target"
            )
    elif kind == "request_permissions":
        if "environment_id" in value and value["environment_id"] is not None:
            _snapshot_text(value["environment_id"], "environment_id")
        if "cwd" in value and value["cwd"] is not None:
            _snapshot_text(value["cwd"], "cwd")
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
        if not isinstance(value.get("permissions"), dict):
            raise ToolApprovalSnapshotRequestError(
                "permission approval permissions are invalid"
            )
    elif kind == "mcp_tool_call":
        for field_name in ("server", "tool_name", "mcp_request_id"):
            _snapshot_text(value.get(field_name), field_name)
        if "arguments" not in value:
            raise ToolApprovalSnapshotRequestError("MCP approval arguments are missing")
        if not _is_json_value(value.get("arguments")):
            raise ToolApprovalSnapshotRequestError("MCP approval arguments are invalid")
        if not isinstance(value.get("reason"), str):
            raise ToolApprovalSnapshotRequestError("approval snapshot reason is invalid")
        annotations = value.get("annotations")
        if annotations is not None:
            if not isinstance(annotations, dict) or set(annotations) - {
                "destructive_hint", "open_world_hint", "read_only_hint"
            }:
                raise ToolApprovalSnapshotRequestError("MCP annotations are invalid")
            if any(
                item is not None and not isinstance(item, bool)
                for item in annotations.values()
            ):
                raise ToolApprovalSnapshotRequestError(
                    "MCP annotations must be boolean or null"
                )
        for field_name in (
            "connector_id", "connector_name", "connector_description",
            "connected_account_email", "tool_title", "tool_description",
        ):
            if field_name in value and value[field_name] is not None:
                _snapshot_text(value[field_name], field_name)


def _validate_snapshot_ack(
    value: dict[str, typing.Any],
    *,
    kind: str,
    decisions: list[typing.Any],
    envelope: dict[str, typing.Any] | None = None,
) -> None:
    """校验快照中的动作回执字段。"""
    if str(value.get("kind") or "").strip() != kind:
        raise ToolApprovalSnapshotRequestError("approval snapshot ack kind is invalid")
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or len(request_id.strip()) < 8:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot ack request_id is invalid"
        )
    decision = value.get("decision")
    if decision not in TOOL_APPROVAL_DECISIONS_BY_KIND[kind] or decision not in decisions:
        raise ToolApprovalSnapshotRequestError("approval snapshot ack decision is invalid")
    expected_tool_status = {
        "decline": "declined", "cancel": "cancelled"
    }.get(decision, "approved")
    if value.get("tool_status") != expected_tool_status:
        raise ToolApprovalSnapshotRequestError("approval snapshot ack tool_status is invalid")
    expected_turn_status = "interrupting" if decision == "cancel" else "active"
    if value.get("turn_status") != expected_turn_status:
        raise ToolApprovalSnapshotRequestError("approval snapshot ack turn_status is invalid")
    contexts = value.get("additional_context")
    if not isinstance(contexts, list) or any(not isinstance(item, str) for item in contexts):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot ack additional_context is invalid"
        )
    if not isinstance(value.get("reason"), str):
        raise ToolApprovalSnapshotRequestError("approval snapshot ack reason is invalid")
    if kind == "command":
        amendment_id = value.get("execpolicy_amendment_id")
        if decision == "acceptWithExecpolicyAmendment":
            if not isinstance(amendment_id, str) or not amendment_id.strip():
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack execpolicy amendment is missing"
                )
        elif amendment_id is not None:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot ack execpolicy amendment is not allowed"
            )
    if kind == "network_access":
        target = _snapshot_text(value.get("target"), "ack target")
        host = _snapshot_text(value.get("host"), "ack host")
        if value.get("protocol") not in {"http", "https", "socks5_tcp", "socks5_udp"}:
            raise ToolApprovalSnapshotRequestError("approval snapshot ack protocol is invalid")
        port = value.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ToolApprovalSnapshotRequestError("approval snapshot ack port is invalid")
        if envelope is not None and (
            target != envelope.get("target")
            or host != envelope.get("host")
            or value.get("protocol") != envelope.get("protocol")
            or port != envelope.get("port")
        ):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot ack network target does not match approval"
            )
        amendment = value.get("network_policy_amendment")
        if decision == "applyNetworkPolicyAmendment":
            if (
                not isinstance(amendment, dict)
                or set(amendment) != {"host", "action"}
                or amendment.get("host") != host
                or amendment.get("action") not in {"allow", "deny"}
            ):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack network policy amendment is invalid"
                )
            if envelope is not None and amendment != envelope.get(
                "proposed_network_policy_amendment"
            ):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack network policy does not match proposal"
                )
        elif amendment is not None:
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot ack network policy amendment is not allowed"
            )
    elif kind == "request_permissions":
        granting = decision not in {"decline", "cancel"}
        fields = (value.get("scope"), value.get("permissions"), value.get("strict_auto_review"))
        if granting:
            if value.get("scope") not in {"turn", "session"} or not isinstance(value.get("permissions"), dict):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack permission grant is incomplete"
                )
            if envelope is not None and not _json_contains(
                envelope.get("permissions"), value.get("permissions")
            ):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack permissions exceed approval"
                )
            if not isinstance(value.get("strict_auto_review"), bool):
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack strict_auto_review is invalid"
                )
            expected_scope = "session" if decision == "grantForSession" else "turn"
            expected_strict = decision == "grantForTurnWithStrictAutoReview"
            if value.get("scope") != expected_scope or value.get("strict_auto_review") is not expected_strict:
                raise ToolApprovalSnapshotRequestError(
                    "approval snapshot ack permission grant is inconsistent"
                )
        elif any(item is not None for item in fields):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot ack permission fields are not allowed"
            )
    elif kind == "mcp_tool_call":
        server = _snapshot_text(value.get("server"), "ack server")
        tool_name = _snapshot_text(value.get("tool_name"), "ack tool_name")
        mcp_request_id = _snapshot_text(value.get("mcp_request_id"), "ack mcp_request_id")
        if "arguments" not in value:
            raise ToolApprovalSnapshotRequestError("MCP approval ack arguments are missing")
        if not _is_json_value(value.get("arguments")):
            raise ToolApprovalSnapshotRequestError("MCP approval ack arguments are invalid")
        if envelope is not None and (
            server != envelope.get("server")
            or tool_name != envelope.get("tool_name")
            or value.get("arguments") != envelope.get("arguments")
            or mcp_request_id != envelope.get("mcp_request_id")
        ):
            raise ToolApprovalSnapshotRequestError(
                "approval snapshot ack MCP request does not match approval"
            )


def _is_json_value(value: typing.Any) -> bool:
    """判断值是否可由 JSON 表示。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def _json_contains(container: typing.Any, candidate: typing.Any) -> bool:
    """判断结构化权限申请是否包含授权内容。"""
    if isinstance(candidate, dict):
        return isinstance(container, dict) and all(
            key in container and _json_contains(container[key], item)
            for key, item in candidate.items()
        )
    if isinstance(candidate, list):
        return isinstance(container, list) and all(
            any(_json_contains(item, wanted) for item in container)
            for wanted in candidate
        )
    return container == candidate


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: typing.Mapping[str, typing.Any],
    additional_context: typing.Sequence[str] = (),
    request_id: str | None = None
) -> dict[str, typing.Any]:
    """把工具执行结果回传给服务端主循环。"""
    headers = build_service_headers()

    payload = build_tool_result_payload(
        cid=cid,
        sid=sid,
        call_id=call_id,
        name=name,
        ok=ok,
        result=result,
        additional_context=additional_context,
        request_id=request_id,
    )

    try:
        r = await post_json_reliably(
            service_endpoints.endpoint("/tool-result"),
            headers=headers,
            payload=payload,
            timeout=30.0,
            client_factory=httpx.AsyncClient,
        )
    except (httpx.HTTPError, OSError) as error:
        raise ToolResultRequestError(
            "tool_result_transport_error",
            "tool result delivery could not reach the service",
            retryable=True,
        ) from error
    if r.is_error:
        raise _tool_result_error(r)
    return _tool_result_ack(r, request_id=payload["request_id"])


def _tool_result_error(response: httpx.Response) -> ToolResultRequestError:
    """把服务端结果投递错误解析为稳定的客户端异常。"""
    try:
        body = response.json()
    except (TypeError, ValueError):
        body = None
    details = body.get("details") if isinstance(body, dict) else None
    if not isinstance(details, dict):
        details = {}
    code = str(details.get("code") or "tool_result_http_error").strip()
    message = str(details.get("message") or "tool result delivery failed").strip()
    return ToolResultRequestError(
        code,
        message,
        status_code=response.status_code,
        retryable=details.get("retryable") is True,
        details=details,
        trace_id=(body.get("trace_id") if isinstance(body, dict) else ""),
    )


def _tool_result_ack(
    response: httpx.Response,
    *,
    request_id: str,
) -> dict[str, typing.Any]:
    """校验工具结果成功回执并确认其对应当前请求。"""
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolResultRequestError(
            "tool_result_ack_invalid",
            "tool result returned an invalid response",
            status_code=response.status_code,
        ) from error
    data = body.get("data") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or not isinstance(data, dict)
        or data.get("status") not in {"matched", "already_received"}
        or data.get("delivered") is not True
        or not isinstance(data.get("already_received"), bool)
        or str(data.get("request_id") or "").strip() != request_id
    ):
        raise ToolResultRequestError(
            "tool_result_ack_mismatch",
            "tool result acknowledgement does not match request",
            status_code=response.status_code,
        )
    return dict(body)


async def get_tool_result_status(
    *,
    cid: str,
    sid: str,
    call_id: str,
    timeout: float = 10.0,
    retry_delays: typing.Sequence[float] = (0.0, 0.2, 0.5),
) -> dict[str, typing.Any]:
    """读取工具调用的权威持久状态，不修改服务端生命周期。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()
    normalized_call_id = str(call_id or "").strip()
    if not normalized_cid or not normalized_sid or not normalized_call_id:
        raise ValueError("tool result status requires cid, sid and call_id")
    try:
        response = await get_json_reliably(
            service_endpoints.endpoint("/tool-result/status"),
            headers=build_service_headers(),
            params={
                "cid": normalized_cid,
                "sid": normalized_sid,
                "call_id": normalized_call_id,
            },
            timeout=timeout,
            client_factory=httpx.AsyncClient,
            retry_delays=retry_delays,
        )
    except (httpx.HTTPError, OSError) as error:
        raise ToolResultRequestError(
            "tool_result_status_transport_error",
            "tool result status could not be read from the service",
            retryable=True,
        ) from error
    if response.is_error:
        raise _tool_result_error(response)
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolResultRequestError(
            "tool_result_status_invalid",
            "tool result status returned an invalid response",
            status_code=response.status_code,
        ) from error
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(data, dict):
        raise ToolResultRequestError(
            "tool_result_status_invalid",
            "tool result status returned an invalid response",
            status_code=response.status_code,
        )
    completion_mode = data.get("completion_mode")
    execution_deadline_at = data.get("execution_deadline_at")
    if (
        "expires_at" in data
        or "execution_deadline_at" not in data
        or completion_mode not in {"interactive", "execution"}
        or (
            execution_deadline_at is not None
            and (
                not isinstance(execution_deadline_at, str)
                or not execution_deadline_at.strip()
            )
        )
        or (completion_mode == "interactive" and execution_deadline_at is not None)
    ):
        raise ToolResultRequestError(
            "tool_result_status_invalid",
            "tool result status returned an invalid response",
            status_code=response.status_code,
        )
    if (
        str(data.get("cid") or "") != normalized_cid
        or str(data.get("sid") or "") != normalized_sid
        or str(data.get("call_id") or "") != normalized_call_id
        or data.get("tool_status") not in _TOOL_RESULT_STATUS_VALUES
        or not isinstance(data.get("result_received"), bool)
    ):
        raise ToolResultRequestError(
            "tool_result_status_mismatch",
            "tool result status does not match request",
            status_code=response.status_code,
        )
    return dict(data)


async def renew_tool_result(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    call_id: str,
    name: str,
    extension_seconds: int = 60,
    request_id: str | None = None,
    timeout: float = 10.0,
) -> dict[str, typing.Any]:
    """续期仍由 Worker lease 持有的托管工具执行预算。"""
    normalized_request_id = (
        resolve_request_id(request_id, prefix="tool_result_renew")
        if request_id is not None
        else stable_request_id(
            "tool_result_renew", cid, sid, turn_id, call_id, name, extension_seconds
        )
    )
    if isinstance(extension_seconds, bool) or not isinstance(extension_seconds, int):
        raise ValueError("extension_seconds must be an integer")
    payload = {
        "request_id": normalized_request_id,
        "cid": cid,
        "sid": sid,
        "turn_id": turn_id,
        "call_id": call_id,
        "name": name,
        "extension_seconds": extension_seconds,
    }
    try:
        response = await post_json_reliably(
            service_endpoints.endpoint("/tool-result/renew"),
            headers=build_service_headers(),
            payload=payload,
            timeout=timeout,
            client_factory=httpx.AsyncClient,
        )
    except (httpx.HTTPError, OSError) as error:
        raise ToolResultRequestError(
            "tool_result_renew_transport_error",
            "tool result renewal could not reach the service",
            retryable=True,
        ) from error
    if response.is_error:
        raise _tool_result_error(response)
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolResultRequestError(
            "tool_result_renew_invalid",
            "tool result renewal returned an invalid response",
            status_code=response.status_code,
        ) from error
    data = body.get("data") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or not isinstance(data, dict)
        or data.get("status") != "renewed"
        or str(data.get("request_id") or "") != normalized_request_id
    ):
        raise ToolResultRequestError(
            "tool_result_renew_mismatch",
            "tool result renewal acknowledgement does not match request",
            status_code=response.status_code,
        )
    return dict(data)


def build_tool_result_payload(
    *,
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: typing.Mapping[str, typing.Any],
    additional_context: typing.Sequence[str] = (),
    request_id: str | None = None
) -> dict[str, typing.Any]:
    """构建普通投递和效果核对共用的工具结果。"""

    normalized_request_id = (
        resolve_request_id(request_id, prefix="tool_result")
        if request_id is not None
        else stable_request_id("tool_result", cid, sid, call_id)
    )

    payload: _ToolResultPayload = {
        "request_id": normalized_request_id,
        "cid": cid,
        "sid": sid,
        "call_id": call_id,
        "name": name,
        "ok": ok,
        "result": _tool_result_for_server(
            result,
            name=name,
            ok=ok,
        )
    }
    contexts = _normalized_contexts(additional_context)
    payload["additional_context"] = contexts
    return dict(payload)


async def post_tool_approval(
    cid: str,
    sid: str,
    call_id: str,
    approval_id: str,
    decision: str,
    *,
    turn_id: str,
    kind: ToolApprovalKind = "command",
    approval: typing.Mapping[str, typing.Any] | None = None,
    request_id: str | None = None,
    execpolicy_amendment_id: str | None = None,
    reason: str | None = None,
    timeout: float = 60.0,
    additional_context: typing.Sequence[str] = ()
) -> ToolApprovalAck:
    """把用户对服务端审批请求的决定回传给主循环。"""
    clean_decision = str(decision or "").strip()
    clean_turn_id  = str(turn_id or "").strip()
    clean_kind = str(kind or "").strip()

    normalized_request_id = (
        resolve_request_id(request_id, prefix="approval")
        if request_id is not None
        else stable_request_id(
            "approval",
            cid,
            sid,
            clean_turn_id,
            call_id,
            approval_id,
            clean_decision,
            execpolicy_amendment_id,
            reason,
        )
    )

    amendment_id = str(execpolicy_amendment_id or "").strip()
    reason_text  = str(reason or "").strip()

    if not clean_turn_id:
        raise ValueError("tool approval requires turn_id")
    typed_kind = _tool_approval_kind(clean_kind)
    if typed_kind is None:
        raise ValueError("tool approval requires a supported kind")
    typed_decision = _tool_approval_decision(clean_decision)
    if typed_decision is None:
        raise ValueError("tool approval requires a supported decision")
    if typed_decision not in TOOL_APPROVAL_DECISIONS_BY_KIND[typed_kind]:
        raise ValueError("tool approval decision is invalid for kind")
    if clean_decision == "acceptWithExecpolicyAmendment":
        if not amendment_id:
            raise ValueError("exec policy amendment approval requires amendment id")
    elif amendment_id:
        raise ValueError("exec policy amendment id requires amendment decision")
    if reason_text and clean_decision not in {"decline", "cancel"}:
        raise ValueError("tool approval reason requires decline or cancel")

    headers = build_service_headers()

    payload: _ToolApprovalPayload = {
        "request_id" : normalized_request_id,
        "cid"         : cid,
        "sid"         : sid,
        "turn_id"     : clean_turn_id,
        "call_id"     : call_id,
        "approval_id" : approval_id,
        "kind"        : typed_kind,
        "decision"    : typed_decision
    }
    if amendment_id:
        payload["execpolicy_amendment_id"] = amendment_id

    context = dict(approval or {})
    if typed_kind == "network_access":
        target = str(context.get("target") or "").strip()
        if not target:
            raise ValueError("network approval requires target")
        host = str(context.get("host") or "").strip()
        if not host:
            raise ValueError("network approval requires host")
        protocol = _tool_approval_network_protocol(context.get("protocol"))
        if protocol is None:
            raise ValueError("network approval protocol is invalid")
        payload["target"] = target
        payload["host"] = host
        payload["protocol"] = protocol
        port = context.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("network approval port is invalid")
        payload["port"] = port
        if clean_decision == "applyNetworkPolicyAmendment":
            proposal = context.get("proposed_network_policy_amendment")
            if (
                not isinstance(proposal, dict)
                or set(proposal) != {"host", "action"}
                or proposal.get("host") != context.get("host")
                or proposal.get("action") not in {"allow", "deny"}
            ):
                raise ValueError("network policy amendment proposal is missing")
            payload["network_policy_amendment"] = {
                "host": host,
                "action": str(proposal["action"]),
            }
    elif typed_kind == "request_permissions":
        if clean_decision not in {"decline", "cancel"}:
            permissions = context.get("permissions")
            if not isinstance(permissions, dict):
                raise ValueError("permission approval requires permissions")
            payload["permissions"] = dict(permissions)
            scope = "session" if clean_decision == "grantForSession" else "turn"
            payload["scope"] = scope
            payload["strict_auto_review"] = (
                clean_decision == "grantForTurnWithStrictAutoReview"
            )
    elif typed_kind == "mcp_tool_call":
        server = str(context.get("server") or "").strip()
        if not server:
            raise ValueError("MCP approval requires server")
        tool_name = str(context.get("tool_name") or "").strip()
        if not tool_name:
            raise ValueError("MCP approval requires tool_name")
        mcp_request_id = str(context.get("mcp_request_id") or "").strip()
        if not mcp_request_id:
            raise ValueError("MCP approval requires mcp_request_id")
        payload["server"] = server
        payload["tool_name"] = tool_name
        payload["mcp_request_id"] = mcp_request_id
        if "arguments" not in context:
            raise ValueError("MCP approval requires arguments")
        if not _is_json_value(context["arguments"]):
            raise ValueError("MCP approval arguments must be JSON")
        payload["arguments"] = context["arguments"]
    if reason_text:
        payload["reason"] = reason_text

    contexts = _normalized_contexts(additional_context)
    if contexts:
        payload["additional_context"] = contexts

    r = await post_json_reliably(
        service_endpoints.endpoint("/tool-approval"),
        headers=headers,
        payload=dict(payload),
        timeout=timeout,
        client_factory=httpx.AsyncClient,
    )
    if r.is_error:
        code, message = _tool_approval_error(r)
        raise ToolApprovalRequestError(
            code,
            message,
            status_code=r.status_code,
        )
    return _tool_approval_ack(
        r,
        request_id=normalized_request_id,
        turn_id=clean_turn_id,
        approval_id=approval_id,
        call_id=call_id,
        decision=typed_decision,
        kind=typed_kind,
    )


def build_tool_result_envelope(
    *,
    tool: str,
    ok: bool,
    args: typing.Mapping[str, typing.Any],
    text: str,
    attachments: typing.Sequence[typing.Any] = (),
    data: typing.Mapping[str, typing.Any] | None = None,
) -> ToolResultEnvelope:
    """从显式字段构建不携带兼容语义的工具结果信封。"""
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("tool result tool must be a non-empty string")
    if not isinstance(ok, bool):
        raise TypeError("tool result ok must be a boolean")
    if not isinstance(args, typing.Mapping):
        raise TypeError("tool result args must be an object")
    if not isinstance(text, str):
        raise TypeError("tool result text must be a string")
    if not isinstance(attachments, (list, tuple)):
        raise TypeError("tool result attachments must be a list")
    result_data = {} if data is None else data
    if not isinstance(result_data, typing.Mapping):
        raise TypeError("tool result data must be an object")
    removed_fields = sorted(
        _REMOVED_CLOUD_SANDBOX_HANDOFF_FIELDS.intersection(result_data)
    )
    if removed_fields:
        raise ValueError(
            "tool result contains removed cloud sandbox handoff fields: "
            + ", ".join(removed_fields)
        )
    return ToolResultEnvelope(
        ok=ok,
        tool=tool.strip(),
        source="client",
        args=dict(args),
        text=text,
        attachments=list(attachments),
        data=dict(result_data),
    )


def _tool_result_for_server(
    result: typing.Mapping[str, typing.Any],
    *,
    name: str,
    ok: bool,
) -> ToolResultEnvelope:
    """校验工具结果信封与外层调用身份完全一致。"""
    if not isinstance(result, typing.Mapping):
        raise TypeError("tool result must be an object")
    unknown = sorted(set(result).difference(_TOOL_RESULT_ENVELOPE_KEYS))
    if unknown:
        raise ValueError(
            "tool result contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(_TOOL_RESULT_ENVELOPE_KEYS.difference(result))
    if missing:
        raise ValueError(
            "tool result is missing fields: " + ", ".join(missing)
        )
    result_ok = result.get("ok")
    if not isinstance(result_ok, bool):
        raise TypeError("tool result ok must be a boolean")
    if result_ok != ok:
        raise ValueError("tool result ok does not match request ok")
    result_tool = result.get("tool")
    if not isinstance(result_tool, str) or result_tool != name:
        raise ValueError("tool result tool does not match request name")
    if result.get("source") != "client":
        raise ValueError("tool result source must be client")
    args = result.get("args")
    text = result.get("text")
    attachments = result.get("attachments")
    data = result.get("data")
    if not isinstance(args, dict):
        raise TypeError("tool result args must be an object")
    if not isinstance(text, str):
        raise TypeError("tool result text must be a string")
    if not isinstance(attachments, list):
        raise TypeError("tool result attachments must be a list")
    if not isinstance(data, dict):
        raise TypeError("tool result data must be an object")
    return build_tool_result_envelope(
        tool=result_tool,
        ok=result_ok,
        args=args,
        text=text,
        attachments=attachments,
        data=data,
    )


def _tool_approval_ack(
    response: httpx.Response,
    *,
    request_id: str,
    turn_id: str,
    approval_id: str,
    call_id: str,
    decision: ToolApprovalDecision,
    kind: ToolApprovalKind,
) -> ToolApprovalAck:
    """校验嵌套审批信封与当前请求是否严格对应。"""
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval returned an invalid response",
            status_code=response.status_code,
        ) from error

    approval = body.get("approval") if isinstance(body, dict) else None
    response_status = body.get("status") if isinstance(body, dict) else None
    if response_status is not None and response_status not in {"resolved", "duplicate"}:
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval response has invalid status",
            status_code=response.status_code,
        )
    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or str(body.get("request_id") or "").strip() != request_id
        or not isinstance(approval, dict)
        or str(approval.get("turn_id") or "").strip() != turn_id
        or str(approval.get("approval_id") or "").strip() != approval_id
        or str(approval.get("call_id") or "").strip() != call_id
        or str(approval.get("kind") or "").strip() != kind
        or str(approval.get("status") or "").strip() != "resolved"
    ):
        raise ToolApprovalRequestError(
            "approval_ack_mismatch",
            "tool approval response does not match request",
            status_code=response.status_code,
        )

    try:
        envelope = _approval_snapshot_item(
            approval,
            expected_turn_id=turn_id,
        )
    except ToolApprovalSnapshotRequestError as error:
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            str(error),
            status_code=response.status_code,
        ) from error

    ack = approval.get("ack")
    if not isinstance(ack, dict):
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval response has no acknowledgement",
            status_code=response.status_code,
        )
    if (
        envelope.ack is None
        or str(ack.get("kind") or "").strip() != kind
        or str(ack.get("request_id") or "").strip() != request_id
        or str(ack.get("decision") or "").strip() != decision
    ):
        raise ToolApprovalRequestError(
            "approval_ack_mismatch",
            "tool approval acknowledgement does not match request",
            status_code=response.status_code,
        )

    tool_status = str(ack.get("tool_status") or "").strip()
    turn_status = str(ack.get("turn_status") or "").strip()
    typed_tool_status = _tool_approval_status(tool_status)
    typed_turn_status = _tool_approval_turn_status(turn_status)

    if (
        typed_tool_status is None
        or typed_turn_status is None
    ):
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval response has invalid lifecycle status",
            status_code=response.status_code,
        )

    raw_context = ack.get("additional_context")
    contexts = (
        tuple(item for item in raw_context if isinstance(item, str))
        if isinstance(raw_context, list)
        else ()
    )
    protocol = _tool_approval_network_protocol(ack.get("protocol"))
    scope = _tool_approval_scope(ack.get("scope"))

    return ToolApprovalAck(
        request_id=request_id,
        turn_id=turn_id,
        approval_id=approval_id,
        call_id=call_id,
        decision=decision,
        tool_status=typed_tool_status,
        turn_status=typed_turn_status,
        kind=kind,
        additional_context=contexts,
        reason=str(ack.get("reason") or ""),
        scope=scope,
        permissions=(
            dict(ack["permissions"])
            if isinstance(ack.get("permissions"), dict)
            else None
        ),
        strict_auto_review=(
            ack.get("strict_auto_review")
            if isinstance(ack.get("strict_auto_review"), bool)
            else None
        ),
        target=str(ack.get("target") or "") or None,
        host=str(ack.get("host") or "") or None,
        protocol=protocol,
        port=ack.get("port") if isinstance(ack.get("port"), int) else None,
        network_policy_amendment=(
            dict(ack["network_policy_amendment"])
            if isinstance(ack.get("network_policy_amendment"), dict)
            else None
        ),
        server=str(ack.get("server") or "") or None,
        tool_name=str(ack.get("tool_name") or "") or None,
        arguments=ack.get("arguments"),
        mcp_request_id=str(ack.get("mcp_request_id") or "") or None,
    )


def _tool_approval_error(response: httpx.Response) -> tuple[str, str]:
    """读取审批错误响应中的稳定代码和说明。"""
    try:
        body = response.json()
    except (TypeError, ValueError):
        body = None

    detail = body.get("detail") if isinstance(body, dict) else None

    if isinstance(detail, dict):
        code    = str(detail.get("code") or "").strip()
        message = str(detail.get("message") or detail.get("detail") or "").strip()

        if code:
            return code, message or code

    text = _response_text(response)
    return "tool_approval_failed", text or "tool approval request failed"


def _normalized_contexts(values: typing.Sequence[str]) -> list[str]:
    """规范化请求中携带的附加上下文。"""
    return [text for value in values if (text := value.strip())]


def _response_text(response: httpx.Response) -> str:
    """安全读取 HTTP 响应文本。"""
    try:
        return str(response.text or "").strip()
    except (TypeError, ValueError, AttributeError):
        return ""


if __name__ == '__main__':
    pass
