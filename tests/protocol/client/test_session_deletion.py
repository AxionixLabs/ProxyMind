import asyncio
from unittest.mock import patch

import httpx
import pytest

from protocol.client.session_deletion import (
    SessionDeletionRequestError,
    delete_sessions,
    get_session_deletion,
)
from protocol.schema.session_deletion import (
    SessionDeletionRequest,
    SessionDeletionTarget,
    parse_session_deletion_receipt,
)


ROOT = SessionDeletionTarget("cid_abc_12345678", "sid_abc_def_123456")
CHILD = SessionDeletionTarget("cid_def_12345678", "sid_def_ghi_123456")
COMMAND = SessionDeletionRequest("delete_test", ROOT, (CHILD,))


def receipt(command=COMMAND):
    return {"request_id": command.request_id, **command.root.payload(), "status": "deleted",
            "targets": [target.payload() for target in command.targets]}


@pytest.fixture
def transport():
    requests = []
    responses = []

    def handle(request):
        requests.append(request)
        result = responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    factory = httpx.AsyncClient
    with (
        patch("protocol.client.session_deletion.httpx.AsyncClient", side_effect=lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs)),
        patch("protocol.client.session_deletion.service_endpoints.endpoint", return_value="https://example.test/session/delete"),
        patch("protocol.client.session_deletion.build_service_headers", return_value={"X-App-ID": "test"}),
    ):
        yield requests, responses


@pytest.mark.anyio
async def test_formal_request_and_query_validate_the_complete_receipt(transport):
    import json
    requests, responses = transport
    responses.extend([httpx.Response(200, json=receipt()), httpx.Response(200, json=receipt())])
    deleted = await delete_sessions(COMMAND)
    assert deleted == await get_session_deletion(COMMAND)
    assert deleted.targets == COMMAND.targets
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content) == COMMAND.payload()
    assert requests[0].headers["X-App-ID"] == "test"
    assert requests[1].method == "GET"
    assert dict(requests[1].url.params) == {"request_id": COMMAND.request_id}


@pytest.mark.anyio
@pytest.mark.parametrize("status,code,outcome", [
    (401, "authentication_required", "rejected"), (403, "owner_mismatch", "rejected"),
    (409, "session_busy", "rejected"), (404, "session_missing", "rejected"),
    (409, "request_id_conflict", "rejected"), (422, "request_validation", "rejected"),
    (404, "request_not_found", "unknown"), (503, "session_cleanup_pending", "unknown"),
    (500, "private-secret", "unknown"), (502, "session_missing", "unknown"),
])
async def test_response_classification_does_not_guess_success(transport, status, code, outcome):
    requests, responses = transport
    responses.append(httpx.Response(status, json={"details": {"code": code, "message": "private-secret", "retryable": True}}))
    with pytest.raises(SessionDeletionRequestError) as caught:
        await delete_sessions(COMMAND)
    assert caught.value.outcome == outcome
    assert caught.value.retryable
    assert caught.value.status_code == status
    assert "private-secret" not in str(caught.value)
    assert "private-secret" != caught.value.code
    assert len(requests) == 1


@pytest.mark.anyio
async def test_lost_response_queries_original_identity_without_reposting(transport):
    requests, responses = transport
    responses.extend([httpx.ReadTimeout("private address"), httpx.Response(200, json=receipt())])
    with pytest.raises(SessionDeletionRequestError) as caught:
        await delete_sessions(COMMAND)
    assert caught.value.outcome == "unknown"
    assert (await get_session_deletion(COMMAND)).request_id == COMMAND.request_id
    assert [request.method for request in requests] == ["POST", "GET"]


@pytest.mark.anyio
async def test_missing_query_receipt_and_cancellation_do_not_mean_deleted(transport):
    _, responses = transport
    responses.append(httpx.Response(404, json={"details": {"code": "request_not_found", "retryable": False}}))
    with pytest.raises(SessionDeletionRequestError) as caught:
        await get_session_deletion(COMMAND)
    assert caught.value.outcome == "unknown"
    responses.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await delete_sessions(COMMAND)


@pytest.mark.anyio
@pytest.mark.parametrize("change", [
    {"status": "accepted"}, {"request_id": "another_request"}, {"targets": [ROOT.payload()]},
    {"targets": [ROOT.payload(), CHILD.payload(), CHILD.payload()]},
    {"targets": [{**ROOT.payload(), "extra": True}, CHILD.payload()]},
    {"cid": CHILD.cid}, {"unexpected": True},
])
async def test_malformed_success_is_unknown_and_preserves_frozen_scope(transport, change):
    _, responses = transport
    responses.append(httpx.Response(200, json={**receipt(), **change}))
    with pytest.raises(SessionDeletionRequestError) as caught:
        await delete_sessions(COMMAND)
    assert caught.value.outcome == "unknown"
    assert caught.value.code == "invalid_receipt"
    assert COMMAND.targets == (ROOT, CHILD)


def test_target_request_and_receipt_validation():
    for cid, sid in ((ROOT.cid + " ", ROOT.sid), (ROOT.cid, CHILD.sid), ("../cid", ROOT.sid)):
        with pytest.raises(ValueError):
            SessionDeletionTarget(cid, sid)
    for descendants in ((ROOT,), (CHILD, CHILD), (CHILD,) * 256, [CHILD]):
        with pytest.raises(ValueError):
            SessionDeletionRequest("delete_test", ROOT, descendants)
    with pytest.raises(ValueError):
        SessionDeletionRequest("bad", ROOT)
    for body in (None, [], True, "deleted", {"ok": True, "data": receipt()}):
        with pytest.raises(ValueError):
            parse_session_deletion_receipt(body, COMMAND)
