# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from datetime import (
    datetime,
    timezone
)

from mind_core import authorize


class DummyHttpClient(object):
    """提供授权输出测试所需的异步客户端上下文。"""

    async def __aenter__(self) -> "DummyHttpClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def test_receive_license_emits_masked_data(monkeypatch, tmp_path: Path) -> None:
    """授权响应通过显式回调发送脱敏数据。"""
    bootstrap_data = {"stage": "bootstrap"}
    activation_data = {"stage": "activation"}
    emitted: list[dict] = []

    monkeypatch.setattr(authorize.Channel, "make_headers", lambda: {})
    monkeypatch.setattr(authorize.Channel, "make_params", lambda: {})
    monkeypatch.setattr(authorize.httpx, "AsyncClient", lambda **kwargs: DummyHttpClient())
    monkeypatch.setattr(authorize.logger, "info", lambda *_: None)

    async def fake_send(client, method: str, url: str, *args, **kwargs):
        _ = client, url, args, kwargs
        return bootstrap_data if method == "GET" else activation_data

    def fake_verify_signature(data):
        if data is bootstrap_data:
            return {"url": "https://authorization.example/bootstrap"}
        return {
            "code": "activation-code-secret",
            "castle": "device-fingerprint-secret",
            "license_id": "license-identifier-secret",
        }

    async def fake_save(lic_file: Path, lic_data) -> int:
        assert lic_data is activation_data
        return 1

    monkeypatch.setattr(authorize, "send", fake_send)
    monkeypatch.setattr(authorize, "verify_signature", fake_verify_signature)
    monkeypatch.setattr(authorize, "save_lic_file", fake_save)

    license_path = tmp_path / "license.json"
    result = asyncio.run(authorize.receive_license(
        "activation-code-secret",
        license_path,
        emit_data=emitted.append,
    ))

    assert result == license_path
    assert len(emitted) == 2
    assert emitted[0]["url"].startswith("https")
    assert "authorization.example" not in emitted[0]["url"]
    assert emitted[1]["code"].startswith("activation")
    assert emitted[1]["castle"].startswith("device-fin")
    assert emitted[1]["license_id"].startswith("license-id")
    assert "secret" not in "".join(emitted[1].values())


def test_verify_license_forwards_data_emitter_on_renewal(monkeypatch, tmp_path: Path) -> None:
    """本地授权续签时继续传递显式数据回调。"""
    license_path = tmp_path / "license.json"
    license_path.write_text("{}", encoding="utf-8")
    emitted: list[dict] = []
    received_emitters = []

    monkeypatch.setattr(authorize.logger, "info", lambda *_: None)
    monkeypatch.setattr(authorize, "verify_signature", lambda *_: {
        "expire": "2099-01-01",
        "code": "renew-code",
        "issued": "2000-01-01T00:00:00+00:00",
        "interval": 1,
    })
    monkeypatch.setattr(
        authorize,
        "network_time",
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def fake_receive(code: str, lic_file: Path, *, emit_data=None):
        assert code == "renew-code"
        assert lic_file == license_path
        received_emitters.append(emit_data)
        return lic_file

    monkeypatch.setattr(authorize, "receive_license", fake_receive)

    result = asyncio.run(authorize.verify_license(
        license_path,
        emit_data=emitted.append,
    ))

    assert result == license_path
    assert received_emitters == [emitted.append]
