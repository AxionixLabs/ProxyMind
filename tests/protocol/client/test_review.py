# -*- coding: utf-8 -*-

from unittest.mock import (
    AsyncMock,
    Mock,
)

import httpx
import pytest

from protocol.client import review
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewRequest,
    ReviewCustomTarget,
    ReviewExecutionOptions,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REQUEST_ID = "review_request_01"


def _tools() -> tuple[dict, ...]:
    """构造最小严格只读工具目录。"""
    return ({
        "name": "exec_command",
        "description": "Read a UTF-8 repository file.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },)


def _request() -> MindReviewRequest:
    """构造最小 inline Review 请求。"""
    return MindReviewRequest(
        session_mode="existing",
        request_id=REQUEST_ID,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Review the current architecture."),
        workspace=ClientReviewWorkspace.create(),
        execution=ReviewExecutionOptions(
            llm_conf={"primary": {"model": "test"}},
            tools=_tools(),
            metadata={"cid": CID, "sid": SID},
        ),
    )


def _response(
    *,
    status_code: int = 202,
    status: str = "accepted",
    cid: str = CID,
    sid: str = SID,
    request_id: str = REQUEST_ID,
) -> httpx.Response:
    """构造 `/mind-review` 回执。"""
    return httpx.Response(
        status_code,
        json={
            "ok": True,
            "data": {
                "request_id": request_id,
                "status": status,
                "cid": CID,
                "sid": SID,
                "turn_id": TURN_ID,
                "review_session": {"cid": cid, "sid": sid},
                "delivery": "inline",
            },
        },
        request=httpx.Request("POST", "https://example.com/mind-review"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", ("accepted", "idempotent"))
async def test_submit_review_reliably_registers_then_observes_receipt_turn(
    monkeypatch,
    status: str,
) -> None:
    request = _request()
    response = _response(status=status)
    post = AsyncMock(return_value=response)
    event_stream = Mock()
    observe = Mock(return_value=event_stream)
    monkeypatch.setattr(review, "post_json_reliably", post)
    monkeypatch.setattr(review, "observe_turn", observe)
    monkeypatch.setattr(
        review.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(
        review,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )

    submission = await review.submit_review(
        request,
        timeout=4.0,
        initial_event_seq=3,
        replay_target_seq=7,
    )

    assert submission.receipt.status == status
    assert submission.events is event_stream
    post.assert_awaited_once_with(
        "https://example.com/mind-review",
        headers={"authorization": "test"},
        payload=request.request_payload(),
        timeout=4.0,
    )
    observe.assert_called_once_with(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        timeout=4.0,
        on_recovery_status=None,
        on_approval_snapshot=None,
        initial_event_seq=3,
        replay_target_seq=7,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    (
        (403, "owner_mismatch", False),
        (404, "endpoint_not_found", False),
        (409, "request_id_conflict", False),
        (409, "turn_already_active", False),
        (409, "turn_id_reused", False),
        (503, "runtime_unavailable", True),
    ),
)
async def test_submit_review_maps_formal_http_failures(
    monkeypatch,
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    response = httpx.Response(
        status_code,
        json={"detail": {"code": code, "message": "review rejected"}},
        request=httpx.Request("POST", "https://example.com/mind-review"),
    )
    monkeypatch.setattr(
        review,
        "post_json_reliably",
        AsyncMock(return_value=response),
    )

    with pytest.raises(review.ReviewRequestError) as raised:
        await review.submit_review(_request())

    assert raised.value.status_code == status_code
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.submission_unknown is False


@pytest.mark.anyio
async def test_submit_review_keeps_http_classification_without_json(
    monkeypatch,
) -> None:
    response = httpx.Response(
        503,
        text="service unavailable",
        request=httpx.Request("POST", "https://example.com/mind-review"),
    )
    monkeypatch.setattr(
        review,
        "post_json_reliably",
        AsyncMock(return_value=response),
    )

    with pytest.raises(review.ReviewRequestError) as raised:
        await review.submit_review(_request())

    assert raised.value.status_code == 503
    assert raised.value.retryable is True


@pytest.mark.anyio
async def test_submit_review_marks_exhausted_transport_as_unknown(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        review,
        "post_json_reliably",
        AsyncMock(side_effect=httpx.ConnectError("connection lost")),
    )

    with pytest.raises(review.ReviewRequestError) as raised:
        await review.submit_review(_request())

    assert raised.value.retryable is True
    assert raised.value.submission_unknown is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    (
        _response(status_code=200),
        _response(request_id="another_request_01"),
        _response(cid="cid_other_87654321", sid="sid_other_x_123456"),
    ),
)
async def test_submit_review_rejects_unexpected_status_or_mismatched_receipt(
    monkeypatch,
    response: httpx.Response,
) -> None:
    observe = Mock()
    monkeypatch.setattr(
        review,
        "post_json_reliably",
        AsyncMock(return_value=response),
    )
    monkeypatch.setattr(review, "observe_turn", observe)

    with pytest.raises(review.ReviewRequestError):
        await review.submit_review(_request())

    observe.assert_not_called()


@pytest.mark.anyio
async def test_submit_review_rejects_unknown_receipt_fields(monkeypatch) -> None:
    response = _response()
    body = response.json()
    body["data"]["legacy"] = True
    response = httpx.Response(
        202,
        json=body,
        request=httpx.Request("POST", "https://example.com/mind-review"),
    )
    monkeypatch.setattr(
        review,
        "post_json_reliably",
        AsyncMock(return_value=response),
    )

    with pytest.raises(review.ReviewRequestError, match="invalid response"):
        await review.submit_review(_request())
