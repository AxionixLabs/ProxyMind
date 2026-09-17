import asyncio
import contextlib
import ssl

import httpx
import pytest

from mcp import (
    ClientSession,
    types as mcp_types,
)
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.initialize_retry import is_retryable_initialize_error


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 400, 401, 403, 404, 409, 501])
def test_initialize_status_classification(status: int) -> None:
    request = httpx.Request("POST", "https://example.test/mcp")
    error = httpx.HTTPStatusError("fixture", request=request, response=httpx.Response(status, request=request))
    assert is_retryable_initialize_error(error) == (status in {408, 429, 500, 502, 503, 504})


@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectError("fixture"), True), (httpx.ReadError("fixture"), True),
    (httpx.WriteError("fixture"), True), (httpx.ReadTimeout("fixture"), True),
    (httpx.CloseError("fixture"), False), (httpx.RemoteProtocolError("fixture"), False),
    (ValueError("fixture"), False), (asyncio.CancelledError(), False),
    (ExceptionGroup("fixture", [httpx.ConnectError("fixture"), ValueError("fixture")]), False),
    (ExceptionGroup("fixture", [httpx.ConnectError("fixture")]), True),
])
def test_only_transient_transport_errors_are_retryable(error: BaseException, expected: bool) -> None:
    assert is_retryable_initialize_error(error) == expected


def test_certificate_failure_is_not_retried() -> None:
    error = httpx.ConnectError("fixture")
    error.__cause__ = ssl.SSLCertVerificationError("fixture")
    assert not is_retryable_initialize_error(error)


@pytest.mark.anyio
async def test_each_retry_closes_previous_resources_in_same_owner(monkeypatch) -> None:
    events: list[str] = []
    owners: set[asyncio.Task] = set()
    session = MagicMock(spec=ClientSession)
    session.get_server_capabilities.return_value = mcp_types.ServerCapabilities()

    @contextlib.asynccontextmanager
    async def resource(attempt: int):
        owner = asyncio.current_task()
        assert owner is not None
        owners.add(owner)
        events.append(f"open{attempt}")
        try:
            yield
        finally:
            assert asyncio.current_task() is owner
            events.append(f"close{attempt}")

    async def establish(_params, _session_params, disconnected, stack, *, config_key, auth):
        attempt = len([event for event in events if event.startswith("open")]) + 1
        await stack.enter_async_context(resource(attempt))
        if attempt < 3:
            disconnected.set()
            raise httpx.ConnectError("fixture")
        assert not disconnected.is_set()
        return mcp_types.Implementation(name="retry", version="1"), session, stack

    monkeypatch.setattr(ExternalMcpGroup, "_establish_session", staticmethod(establish))
    group = ExternalMcpGroup()
    try:
        await group.connect_with_alias({"name": "retry", "transport": "streamable_http", "url": "https://example.test/mcp"})
        assert events == ["open1", "close1", "open2", "close2", "open3"]
    finally:
        await group.close()
    assert events[-1] == "close3" and len(owners) == 1
    assert not group.owned_keys


@pytest.mark.anyio
async def test_retry_is_aborted_if_resource_close_fails(monkeypatch) -> None:
    attempts: list[int] = []

    async def close() -> None:
        raise httpx.CloseError("fixture close failed")

    async def establish(_params, _session_params, _disconnected, stack, *, config_key, auth):
        attempts.append(1)
        stack.push_async_callback(close)
        raise httpx.ConnectError("fixture")

    monkeypatch.setattr(ExternalMcpGroup, "_establish_session", staticmethod(establish))
    group = ExternalMcpGroup()
    with pytest.raises(RuntimeError, match="fixture close failed"):
        await group.connect_with_alias({"name": "retry", "transport": "streamable_http", "url": "https://example.test/mcp"})
    assert len(attempts) == 1
    assert group.owned_keys == frozenset({"retry"})


@pytest.mark.anyio
async def test_catalog_failure_does_not_restart_initialize(monkeypatch) -> None:
    establish = AsyncMock(return_value=(
        mcp_types.Implementation(name="retry", version="1"), MagicMock(spec=ClientSession), contextlib.AsyncExitStack(),
    ))
    collect = AsyncMock(side_effect=httpx.ReadError("fixture discovery failed"))
    monkeypatch.setattr(ExternalMcpGroup, "_establish_session", establish)
    monkeypatch.setattr(ExternalMcpGroup, "_collect_tools", collect)
    group = ExternalMcpGroup()
    with pytest.raises(httpx.ReadError):
        await group.connect_with_alias({"name": "retry", "transport": "streamable_http", "url": "https://example.test/mcp"})
    await group.close()
    assert establish.await_count == 1
