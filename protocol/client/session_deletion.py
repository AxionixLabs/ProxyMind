# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

import httpx

from protocol.schema.session_deletion import (
    SessionDeletionReceipt,
    SessionDeletionRequest,
    parse_session_deletion_receipt,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints


class SessionDeletionRequestError(Exception):
    """区分本次请求的明确拒绝与无法证明删除结果的失败，不拥有重试生命周期。"""

    def __init__(
        self, *, outcome: typing.Literal["rejected", "unknown"],
        code: str, status_code: int | None = None, retryable: bool = False,
    ) -> None:
        """保存可供上层对账的稳定分类，不转发远端正文和连接凭据。"""
        super().__init__("Session deletion was rejected." if outcome == "rejected" else "Session deletion outcome is unknown; query using the original request ID.")
        self.outcome = outcome
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


async def delete_sessions(command: SessionDeletionRequest, *, timeout: float = 30.0) -> SessionDeletionReceipt:
    """提交一次冻结意图，只有完整且匹配的回执才能证明远端完成。"""
    return await _request(command, query=False, timeout=timeout)


async def get_session_deletion(command: SessionDeletionRequest, *, timeout: float = 30.0) -> SessionDeletionReceipt:
    """查询原删除身份并核对完整目标集合；缺失回执不表示原请求已取消。"""
    return await _request(command, query=True, timeout=timeout)


async def _request(command: SessionDeletionRequest, *, query: bool, timeout: float) -> SessionDeletionReceipt:
    """使用既有鉴权与端点完成一次 HTTP 交换，取消信号由生命周期所有者接管。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            url = service_endpoints.endpoint("/session/delete")
            headers = build_service_headers()
            if query:
                response = await client.get(url, headers=headers, params={"request_id": command.request_id})
            else:
                response = await client.post(url, headers=headers, json=command.payload())
    except (httpx.RequestError, OSError) as error:
        raise SessionDeletionRequestError(outcome="unknown", code="transport_error", retryable=True) from error
    if response.status_code != 200:
        raise _response_error(response, query=query)
    try:
        return parse_session_deletion_receipt(response.json(), command)
    except (ValueError, TypeError) as error:
        raise SessionDeletionRequestError(outcome="unknown", code="invalid_receipt", status_code=200) from error


def _response_error(response: httpx.Response, *, query: bool) -> SessionDeletionRequestError:
    """仅将正式状态码与错误代码组合视为拒绝，其他响应保留未知语义。"""
    code = "unexpected_response"
    retryable = response.status_code >= 500 or response.status_code in {408, 429}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        details = body.get("details")
        if isinstance(details, dict):
            raw_code = details.get("code")
            raw_retryable = details.get("retryable")
            known_codes = {
                "authentication_required", "authentication_invalid", "owner_mismatch",
                "session_missing", "session_busy", "request_id_conflict", "request_not_found",
                "request_validation", "session_cleanup_pending", "runtime_unavailable",
            }
            if isinstance(raw_code, str) and raw_code in known_codes:
                code = raw_code
            if isinstance(raw_retryable, bool):
                retryable = raw_retryable
    rejected_statuses = {
        "authentication_required": 401, "authentication_invalid": 403, "owner_mismatch": 403,
        "session_missing": 404, "session_busy": 409, "request_id_conflict": 409,
        "request_validation": 422,
    }
    rejected = not query and rejected_statuses.get(code) == response.status_code
    return SessionDeletionRequestError(
        outcome="rejected" if rejected else "unknown", code=code,
        status_code=response.status_code, retryable=retryable,
    )


if __name__ == '__main__':
    pass
