# -*- coding: utf-8 -*-

import base64
import hashlib
import hmac
import json

from metadata import const
from protocol.transport import config
from protocol.transport import auth as service_auth


def _decode_token_part(value: str) -> dict[str, object]:
    padding = "=" * (-len(value) % 4)
    return json.loads(base64.b64decode(value + padding))


def test_service_headers_preserve_signed_wire_contract(monkeypatch) -> None:
    monkeypatch.setattr(service_auth.time, "time", lambda: 1_000)
    monkeypatch.setattr(
        service_auth.secrets,
        "token_hex",
        lambda length: "a" * (length * 2),
    )

    headers = service_auth.build_service_headers()

    header_part, payload_part, signature_part = headers["X-App-Token"].split(".")
    assert _decode_token_part(header_part) == {"alg": "HS256", "typ": "JWT"}
    assert _decode_token_part(payload_part) == {
        "app": const.APP_DESC,
        "iat": 1_000,
        "exp": 1_300,
        "jti": "a" * 16,
    }
    expected_signature = hmac.new(
        config.SHARED_SECRET.encode(),
        f"{header_part}.{payload_part}".encode(),
        hashlib.sha256,
    ).digest()
    assert base64.b64decode(signature_part + "=" * (-len(signature_part) % 4)) == (
        expected_signature
    )
    assert headers == {
        "User-Agent": f"{const.APP_DESC}@{const.APP_VERSION}",
        "Content-Type": "application/json",
        "X-App-ID": const.PUBLISHER,
        "X-App-Token": headers["X-App-Token"],
        "X-App-Region": "Global",
        "X-App-Version": f"v{const.APP_VERSION}",
    }


def test_service_query_preserves_common_parameters(monkeypatch) -> None:
    monkeypatch.setattr(service_auth.time, "time", lambda: 1_000)
    monkeypatch.setattr(
        service_auth.secrets,
        "token_hex",
        lambda length: "b" * (length * 2),
    )

    assert service_auth.build_service_query() == {
        "a": const.APP_DESC,
        "t": 1_000,
        "n": "b" * 16,
    }
