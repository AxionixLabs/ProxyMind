import asyncio
from unittest.mock import patch

import httpx
import pytest

from protocol.client.manifest import fetch_manifest


@pytest.fixture
def wire_manifest():
    return {"ok": True, "data": {
        "host": "Mind", "service": "cua", "station": "win32", "arch": "x64", "channel": "stable",
        "version": "1.0.0", "build": "test", "published_at": "now", "notes": "test",
        "package": {"filename": "cua-win-x64.zip", "size": 123, "hash": "a" * 64,
                    "updated_at": "now", "url": "https://downloads.test/cua.zip"},
    }}


@pytest.mark.anyio
@pytest.mark.parametrize("machine", ["AMD64", "x86_64", "x64"])
async def test_manifest_selects_component_and_normalizes_architecture(wire_manifest, machine):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=wire_manifest)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with patch("protocol.client.manifest.httpx.AsyncClient", return_value=client):
        result = await fetch_manifest("cua", station="win32", arch=machine)
    assert result.package.hash == "a" * 64
    assert requests[0].url.params["service"] == "cua" and requests[0].url.params["arch"] == "x64"
    assert client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 500, 503])
async def test_manifest_only_allows_offline_cache_for_service_outage(status):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    with patch("protocol.client.manifest.httpx.AsyncClient", return_value=client):
        if status >= 500:
            assert await fetch_manifest("cua", station="win32", arch="x64") is None
        else:
            with pytest.raises(ValueError, match=f"HTTP {status}"):
                await fetch_manifest("cua", station="win32", arch="x64")
    assert client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [("service", "helix"), ("host", "Other"), ("station", "darwin"),
                                        ("arch", "arm64"), ("version", "../escape")])
async def test_invalid_manifest_identity_does_not_fall_back(wire_manifest, field, value):
    wire_manifest["data"][field] = value
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=wire_manifest)))
    with patch("protocol.client.manifest.httpx.AsyncClient", return_value=client), pytest.raises(ValueError):
        await fetch_manifest("cua", station="win32", arch="x64")


@pytest.mark.anyio
@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), asyncio.CancelledError()])
async def test_network_outage_and_cancel_remain_distinct(error):
    def handle(request):
        raise error

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with patch("protocol.client.manifest.httpx.AsyncClient", return_value=client):
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await fetch_manifest("cua", station="win32", arch="x64")
        else:
            assert await fetch_manifest("cua", station="win32", arch="x64") is None
    assert client.is_closed
