# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

import httpx

from protocol.client.chat import (
    ApprovalSnapshotCallback,
    RecoveryStatusCallback,
    TurnEventStream,
    observe_turn,
)
from protocol.schema.json_value import (
    JsonObject,
)
from protocol.schema.review import (
    MindReviewReceipt,
    MindReviewRequest,
    parse_review_response,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reliable import (
    is_retryable_status,
    post_json_reliably,
)


class ReviewRequestError(Exception):
    """描述 Review 登记请求未得到可确认的有效回执。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        retryable: bool = False,
        submission_unknown: bool = False,
    ) -> None:
        """保存错误分类和远端登记是否可能已经发生。"""
        super().__init__(message)
        self.status_code = status_code
        self.code = code.strip()
        self.retryable = retryable
        self.submission_unknown = submission_unknown


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    """保存已确认的 Review 回执及其既有 Turn 观察流。"""

    receipt: MindReviewReceipt
    events: TurnEventStream


def _response_object(response: httpx.Response) -> JsonObject:
    """读取 JSON 对象响应，无法读取时返回空错误信封。"""
    try:
        value = response.json()
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _error_detail(body: JsonObject) -> tuple[str, str]:
    """读取服务端稳定错误代码和消息。"""
    detail = body.get("detail")
    details = body.get("details")
    envelope: JsonObject | None = None
    if isinstance(detail, dict):
        envelope = detail
    elif isinstance(details, dict):
        envelope = details
    if envelope is not None:
        raw_code = envelope.get("code")
        raw_message = envelope.get("message")
        return (
            raw_code.strip() if isinstance(raw_code, str) else "",
            raw_message.strip() if isinstance(raw_message, str) else "",
        )
    raw_code = body.get("code")
    raw_message = body.get("message")
    if not isinstance(raw_message, str):
        raw_message = detail if isinstance(detail, str) else details
    return (
        raw_code.strip() if isinstance(raw_code, str) else "",
        raw_message.strip() if isinstance(raw_message, str) else "",
    )


def _http_error(response: httpx.Response, body: JsonObject) -> ReviewRequestError:
    """把 Review HTTP 失败映射为稳定客户端错误。"""
    code, detail = _error_detail(body)
    status_code = response.status_code
    default_message = {
        403: "Review source session is not available to this client.",
        404: "Review endpoint is not available.",
        409: "Review conflicts with an existing durable turn.",
        503: "Review service is temporarily unavailable.",
    }.get(status_code, f"Review request failed with HTTP {status_code}.")
    return ReviewRequestError(
        detail or default_message,
        status_code=status_code,
        code=code,
        retryable=is_retryable_status(status_code),
    )


def _validate_receipt(
    receipt: MindReviewReceipt,
    request: MindReviewRequest,
) -> None:
    """确认服务端回执属于冻结请求且交付坐标一致。"""
    if (
        receipt.request_id != request.request_id
        or receipt.cid != request.cid
        or receipt.sid != request.sid
        or receipt.turn_id != request.turn_id
        or receipt.delivery != request.delivery
    ):
        raise ReviewRequestError(
            "Review response does not match the submitted request."
        )
    if request.delivery == "inline" and (
        receipt.review_session.cid != request.cid
        or receipt.review_session.sid != request.sid
    ):
        raise ReviewRequestError(
            "Inline Review response changed the source session."
        )


async def submit_review(
    request: MindReviewRequest,
    *,
    timeout: float = 60.0,
    on_recovery_status: RecoveryStatusCallback | None = None,
    on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    initial_event_seq: int = 0,
    replay_target_seq: int | None = None,
) -> ReviewSubmission:
    """可靠登记 Review，并只观察回执确认的既有 Turn。"""
    try:
        response = await post_json_reliably(
            service_endpoints.endpoint("/mind-review"),
            headers=build_service_headers(),
            payload=request.request_payload(),
            timeout=timeout,
        )
    except (httpx.TransportError, OSError) as error:
        raise ReviewRequestError(
            "Review submission could not be confirmed.",
            retryable=True,
            submission_unknown=True,
        ) from error

    body = _response_object(response)
    if response.status_code >= 400:
        raise _http_error(response, body)
    if response.status_code != 202:
        raise ReviewRequestError(
            "Review request returned an unexpected HTTP status.",
            status_code=response.status_code,
        )
    try:
        receipt = parse_review_response(body)
    except (TypeError, ValueError) as error:
        raise ReviewRequestError(
            "Review request returned an invalid response.",
            status_code=response.status_code,
        ) from error
    _validate_receipt(receipt, request)
    events = observe_turn(
        cid=receipt.review_session.cid,
        sid=receipt.review_session.sid,
        turn_id=receipt.turn_id,
        timeout=timeout,
        on_recovery_status=on_recovery_status,
        on_approval_snapshot=on_approval_snapshot,
        initial_event_seq=initial_event_seq,
        replay_target_seq=replay_target_seq,
    )
    return ReviewSubmission(receipt=receipt, events=events)


if __name__ == '__main__':
    pass
