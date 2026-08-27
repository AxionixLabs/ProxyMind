# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.identifiers import (
    normalize_turn_id,
    resolve_request_id,
    stable_request_id
)
from mind_nova.requests.reliable import post_json_reliably
from mind_nova.services import service_endpoints
from mind_nova.tool_approval import (
    TOOL_APPROVAL_DECISIONS,
    TOOL_APPROVAL_STATUSES,
    TOOL_APPROVAL_TURN_STATUSES,
    ToolApprovalAck,
    ToolApprovalDecision,
    ToolApprovalStatus,
    ToolApprovalTurnStatus,
    ToolApprovalSnapshot,
    ToolApprovalSnapshotItem,
    ToolApprovalSnapshotStatus
)

_ToolResultValue = typing.Union[
    None,
    str,
    int,
    bool,
    float,
    list[typing.Any],
    dict[str, typing.Any],
]

_TOOL_RESULT_ENVELOPE_KEYS = frozenset({
    "ok",
    "text",
    "data",
})

_TOOL_RESULT_METADATA_KEYS = frozenset({
    "ok",
    "tool",
    "source",
    "args",
    "text",
    "attachments",
    "target",
})


class _ServerToolResult(typing.TypedDict):
    """描述服务端接收的规范工具结果。"""
    ok: bool
    tool: str
    source: str
    args: dict[str, typing.Any]
    text: str
    attachments: list[typing.Any]
    data: dict[str, typing.Any]
    target: typing.NotRequired[str]


class _ToolResultPayload(typing.TypedDict):
    """描述工具执行结果的请求载荷。"""
    request_id: str
    cid: str
    sid: str
    call_id: str
    name: str
    ok: bool
    result: _ServerToolResult
    additional_context: typing.NotRequired[list[str]]


class _ToolApprovalPayload(typing.TypedDict):
    """描述工具审批决定的请求载荷。"""
    request_id: str
    cid: str
    sid: str
    turn_id: str
    call_id: str
    approval_id: str
    decision: ToolApprovalDecision
    execpolicy_amendment_id: typing.NotRequired[str]
    reason: typing.NotRequired[str]
    additional_context: typing.NotRequired[list[str]]


class ToolApprovalRequestError(Exception):
    """描述服务端拒绝或无法确认的审批决定。"""

    def __init__(self, code: str, message: str, *, status_code: int = 0) -> None:
        """保存审批请求的错误码和响应状态。"""
        super().__init__(message)
        self.code = code
        self.status_code = status_code


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
                headers=Channel.make_headers(),
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
    expected_turn_id: str,
) -> ToolApprovalSnapshotItem:
    """校验并转换单项审批恢复记录。"""
    if not isinstance(value, dict):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item is invalid"
        )

    def text_field(name: str, *, required: bool = False) -> str:
        result = str(value.get(name) or "").strip()
        if required and not result:
            raise ToolApprovalSnapshotRequestError(
                f"approval snapshot {name} is invalid"
            )
        return result

    approval_id = text_field("approval_id", required=True)
    turn_id = text_field("turn_id", required=True)
    if turn_id != expected_turn_id:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item turn_id does not match request"
        )
    call_id = text_field("call_id", required=True)
    name = text_field("name", required=True)
    raw_arguments = value.get("arguments")
    raw_approval = value.get("approval")
    if (
        not isinstance(raw_arguments, dict)
        or not isinstance(raw_approval, dict)
    ):
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item payload is invalid"
        )

    status = text_field("status", required=True)
    if status not in {"pending", "resolved", "expired", "cancelled"}:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item status is invalid"
        )

    raw_context = value.get("additional_context")
    if raw_context is None:
        additional_context: tuple[str, ...] = ()
    elif isinstance(raw_context, list):
        additional_context = tuple(
            str(item).strip()
            for item in raw_context
            if str(item).strip()
        )
    else:
        raise ToolApprovalSnapshotRequestError(
            "approval snapshot item additional_context is invalid"
        )

    return ToolApprovalSnapshotItem(
        approval_id=approval_id,
        turn_id=turn_id,
        call_id=call_id,
        name=name,
        arguments=dict(raw_arguments),
        approval=dict(raw_approval),
        status=typing.cast(ToolApprovalSnapshotStatus, status),
        decision=text_field("decision"),
        execpolicy_amendment_id=text_field("execpolicy_amendment_id"),
        reason=text_field("reason"),
        additional_context=additional_context,
        ack=(dict(value["ack"]) if isinstance(value.get("ack"), dict) else None),
        expires_at=_snapshot_float(value.get("expires_at"), "expires_at"),
        resolved_at=_snapshot_optional_float(value.get("resolved_at"), "resolved_at"),
        created_at=_snapshot_float(value.get("created_at"), "created_at"),
        updated_at=_snapshot_float(value.get("updated_at"), "updated_at"),
    )


def _snapshot_float(value: typing.Any, field_name: str) -> float:
    """读取快照中的非负时间字段。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
    ):
        raise ToolApprovalSnapshotRequestError(
            f"approval snapshot {field_name} is invalid"
        )
    return float(value)


def _snapshot_optional_float(value: typing.Any, field_name: str) -> float | None:
    """读取快照中的可选非负时间字段。"""
    if value is None:
        return None
    return _snapshot_float(value, field_name)


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: _ToolResultValue,
    additional_context: typing.Sequence[str] = (),
    arguments: typing.Mapping[str, typing.Any] | None = None,
    request_id: str | None = None
) -> dict[str, typing.Any]:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()

    payload = build_tool_result_payload(
        cid=cid,
        sid=sid,
        call_id=call_id,
        name=name,
        ok=ok,
        result=result,
        additional_context=additional_context,
        arguments=arguments,
        request_id=request_id,
    )

    r = await post_json_reliably(
        service_endpoints.endpoint("/tool-result"),
        headers=headers,
        payload=payload,
        timeout=30.0,
        client_factory=httpx.AsyncClient,
    )
    r.raise_for_status()
    return r.json()


def build_tool_result_payload(
    *,
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: _ToolResultValue,
    additional_context: typing.Sequence[str] = (),
    arguments: typing.Mapping[str, typing.Any] | None = None,
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
            arguments=arguments,
        )
    }
    contexts = _normalized_contexts(additional_context)
    if contexts:
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
    request_id: str | None = None,
    execpolicy_amendment_id: str | None = None,
    reason: str | None = None,
    timeout: float = 60.0,
    additional_context: typing.Sequence[str] = ()
) -> ToolApprovalAck:
    """把用户对服务端审批请求的决定回传给主循环。"""
    clean_decision = str(decision or "").strip()
    clean_turn_id  = str(turn_id or "").strip()

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
    if clean_decision not in TOOL_APPROVAL_DECISIONS:
        raise ValueError("tool approval requires a supported decision")
    typed_decision = typing.cast(ToolApprovalDecision, clean_decision)
    if clean_decision == "acceptWithExecpolicyAmendment":
        if not amendment_id:
            raise ValueError("exec policy amendment approval requires amendment id")
    elif amendment_id:
        raise ValueError("exec policy amendment id requires amendment decision")
    if reason_text and clean_decision not in {"decline", "cancel"}:
        raise ValueError("tool approval reason requires decline or cancel")

    headers = Channel.make_headers()

    payload: _ToolApprovalPayload = {
        "request_id" : normalized_request_id,
        "cid"         : cid,
        "sid"         : sid,
        "turn_id"     : clean_turn_id,
        "call_id"     : call_id,
        "approval_id" : approval_id,
        "decision"    : typed_decision
    }
    if amendment_id:
        payload["execpolicy_amendment_id"] = amendment_id
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
    )


def _tool_result_for_server(
    result: _ToolResultValue,
    *,
    name: str,
    ok: bool,
    arguments: typing.Mapping[str, typing.Any] | None
) -> _ServerToolResult:
    """将内部工具结果投影为服务端传输结构。"""
    if not isinstance(result, dict):
        return {
            "ok": bool(ok),
            "tool": str(name or ""),
            "source": "client",
            "args": dict(arguments or {}),
            "text": result if isinstance(result, str) else "",
            "attachments": [],
            "data": {"value": result}
        }

    text = str(result.get("text") or result.get("error") or "")

    raw_attachments = result.get("attachments")

    attachments = (
        list(raw_attachments)
        if isinstance(raw_attachments, (list, tuple))
        else []
    )

    if _TOOL_RESULT_ENVELOPE_KEYS.issubset(result):
        raw_data = result.get("data")
        data = raw_data if isinstance(raw_data, dict) else {"value": raw_data}
    else:
        data = {
            key: value
            for key, value in result.items()
            if key not in _TOOL_RESULT_METADATA_KEYS
        }

    raw_args    = result.get("args")
    result_args = raw_args if isinstance(raw_args, dict) else {}

    payload: _ServerToolResult = {
        "ok": bool(ok),
        "tool": str(name or ""),
        "source": str(result.get("source") or "client"),
        "args": dict(arguments) if arguments is not None else dict(result_args),
        "text": text,
        "attachments": attachments,
        "data": data,
    }

    target = str(result.get("target") or "").strip()
    if target:
        payload["target"] = target

    return payload


def _tool_approval_ack(
    response: httpx.Response,
    *,
    request_id: str,
    turn_id: str,
    approval_id: str,
    call_id: str,
    decision: ToolApprovalDecision
) -> ToolApprovalAck:
    """校验审批响应与当前请求是否严格对应。"""
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval returned an invalid response",
            status_code=response.status_code,
        ) from error

    expected = {
        "request_id": request_id,
        "turn_id": turn_id,
        "approval_id": approval_id,
        "call_id": call_id,
        "decision": decision,
    }

    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or any(str(body.get(key) or "").strip() != value for key, value in expected.items())
    ):
        raise ToolApprovalRequestError(
            "approval_ack_mismatch",
            "tool approval response does not match request",
            status_code=response.status_code,
        )

    tool_status = str(body.get("tool_status") or "").strip()
    turn_status = str(body.get("turn_status") or "").strip()

    if (
        tool_status not in TOOL_APPROVAL_STATUSES
        or turn_status not in TOOL_APPROVAL_TURN_STATUSES
    ):
        raise ToolApprovalRequestError(
            "approval_ack_invalid",
            "tool approval response has invalid lifecycle status",
            status_code=response.status_code,
        )

    return ToolApprovalAck(
        request_id=request_id,
        turn_id=turn_id,
        approval_id=approval_id,
        call_id=call_id,
        decision=decision,
        tool_status=typing.cast(ToolApprovalStatus, tool_status),
        turn_status=typing.cast(ToolApprovalTurnStatus, turn_status),
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
