# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.identifiers import resolve_request_id
from mind_nova.services import service_endpoints
from mind_nova.tool_approval import (
    TOOL_APPROVAL_DECISIONS,
    TOOL_APPROVAL_STATUSES,
    TOOL_APPROVAL_TURN_STATUSES,
    ToolApprovalAck,
    ToolApprovalDecision,
    ToolApprovalStatus,
    ToolApprovalTurnStatus
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
    execution: typing.NotRequired[dict[str, typing.Any]]
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


class ToolApprovalExpired(Exception):
    """表示服务端审批请求已不再处于 pending 状态。"""

    def __init__(self, message: str = "tool approval not pending") -> None:
        super().__init__(message)


class ToolApprovalRequestError(Exception):
    """描述服务端拒绝或无法确认的审批决定。"""

    def __init__(self, code: str, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: _ToolResultValue,
    execution: dict[str, typing.Any] | None = None,
    additional_context: typing.Sequence[str] = (),
    arguments: typing.Mapping[str, typing.Any] | None = None,
    request_id: str | None = None
) -> dict[str, typing.Any]:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()

    normalized_request_id = resolve_request_id(
        request_id,
        prefix="tool_result",
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
    if isinstance(execution, dict):
        payload["execution"] = execution

    contexts = _normalized_contexts(additional_context)
    if contexts:
        payload["additional_context"] = contexts

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(service_endpoints.endpoint("/tool-result"), headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


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

    normalized_request_id = resolve_request_id(
        request_id,
        prefix="approval",
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

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(service_endpoints.endpoint("/tool-approval"), headers=headers, json=payload)
        if r.is_error:
            code, message = _tool_approval_error(r)
            if r.status_code == 404 and code == "approval_not_pending":
                raise ToolApprovalExpired(message)
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
