# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.services import endpoint


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
    result: typing.Union[
        None,
        str,
        int,
        bool,
        float,
        list[typing.Any],
        dict[str, typing.Any]
    ],
    execution: dict[str, typing.Any] | None = None
) -> None:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()
    payload = {
        "cid"     : cid,
        "sid"     : sid,
        "call_id" : call_id,
        "name"    : name,
        "ok"      : ok,
        "result"  : result
    }
    if isinstance(execution, dict):
        payload["execution"] = execution

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(endpoint("/tool-result"), headers=headers, json=payload)
        r.raise_for_status()


async def post_tool_approval(
    cid: str,
    sid: str,
    call_id: str,
    approval_id: str,
    decision: str,
    reason: str | None = None,
    timeout: float = 60.0
) -> None:
    """把用户对服务端审批请求的决定回传给主循环。"""
    clean_decision = str(decision or "").strip() or "decline"
    headers = Channel.make_headers()
    payload = {
        "cid"         : cid,
        "sid"         : sid,
        "call_id"     : call_id,
        "approval_id" : approval_id,
        "decision"    : clean_decision
    }
    if reason:
        payload["reason"] = reason

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(endpoint("/tool-approval"), headers=headers, json=payload)
        if _tool_approval_expired_response(r):
            raise ToolApprovalExpired(_response_text(r) or "tool approval not pending")
        r.raise_for_status()


def _tool_approval_expired_response(response: httpx.Response) -> bool:
    """识别服务端返回的审批已过期/不再 pending 响应。"""
    if response.status_code != 404:
        return False
    text = _response_text(response).lower()
    return "tool approval not pending" in text or "approval not pending" in text


def _response_text(response: httpx.Response) -> str:
    """安全读取 HTTP 响应文本。"""
    try:
        return str(response.text or "").strip()
    except (TypeError, ValueError, AttributeError):
        return ""


if __name__ == '__main__':
    pass
