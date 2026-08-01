# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.services import service_endpoints


_ToolResultValue = typing.Union[
    None,
    str,
    int,
    bool,
    float,
    list[typing.Any],
    dict[str, typing.Any],
]


class _ToolResultPayload(typing.TypedDict):
    """描述工具执行结果的请求载荷。"""
    cid: str
    sid: str
    call_id: str
    name: str
    ok: bool
    result: _ToolResultValue
    execution: typing.NotRequired[dict[str, typing.Any]]
    additional_context: typing.NotRequired[list[str]]
    system_message: typing.NotRequired[str]


class _ToolApprovalPayload(typing.TypedDict):
    """描述工具审批决定的请求载荷。"""
    cid: str
    sid: str
    call_id: str
    approval_id: str
    decision: str
    reason: typing.NotRequired[str]
    additional_context: typing.NotRequired[list[str]]


class ToolApprovalExpired(Exception):
    """表示服务端审批请求已不再处于 pending 状态。"""

    def __init__(self, message: str = "tool approval not pending") -> None:
        super().__init__(message)
        self.message = message


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: _ToolResultValue,
    execution: dict[str, typing.Any] | None = None,
    additional_context: typing.Sequence[str] = (),
    system_message: str = ""
) -> dict[str, typing.Any]:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()
    payload: _ToolResultPayload = {
        "cid"     : cid,
        "sid"     : sid,
        "call_id" : call_id,
        "name"    : name,
        "ok"      : ok,
        "result"  : result
    }
    if isinstance(execution, dict):
        payload["execution"] = execution

    contexts = _normalized_contexts(additional_context)
    if contexts:
        payload["additional_context"] = contexts

    system_text = str(system_message or "").strip()
    if system_text:
        payload["system_message"] = system_text

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
    reason: str | None = None,
    timeout: float = 60.0,
    additional_context: typing.Sequence[str] = (),
) -> None:
    """把用户对服务端审批请求的决定回传给主循环。"""
    clean_decision = str(decision or "").strip() or "decline"
    headers = Channel.make_headers()
    payload: _ToolApprovalPayload = {
        "cid"         : cid,
        "sid"         : sid,
        "call_id"     : call_id,
        "approval_id" : approval_id,
        "decision"    : clean_decision
    }
    if reason:
        payload["reason"] = reason

    contexts = _normalized_contexts(additional_context)
    if contexts:
        payload["additional_context"] = contexts

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(service_endpoints.endpoint("/tool-approval"), headers=headers, json=payload)
        if _tool_approval_expired_response(r):
            raise ToolApprovalExpired(_response_text(r) or "tool approval not pending")
        r.raise_for_status()


def _tool_approval_expired_response(response: httpx.Response) -> bool:
    """识别服务端返回的审批已过期/不再 pending 响应。"""
    if response.status_code != 404:
        return False
    text = _response_text(response).lower()
    return "tool approval not pending" in text or "approval not pending" in text


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
