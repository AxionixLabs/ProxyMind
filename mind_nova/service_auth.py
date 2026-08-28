# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import base64
import hashlib
import hmac
import json
import secrets
import time
import typing

import jwt

from mind_nova import const


def derive_hs256_secret(*, step_sec: int = 300, ts: int | None = None) -> str:
    """按时间窗口派生本地服务使用的 HS256 密钥。"""
    if ts is None:
        ts = int(time.time())

    bucket = ts // step_sec

    msg = str(bucket).encode(const.CHARSET)
    key = const.MASTER.encode(const.CHARSET)

    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode(const.CHARSET).rstrip("=")


def manufacture_token(ttl_sec: int = 3600) -> str:
    """生成本地服务认证使用的短期令牌。"""
    now    = int(time.time())
    secret = derive_hs256_secret(ts=now)

    payload = {
        "iss"   : const.ISSUER,
        "aud"   : const.AUDIENCE,
        "sub"   : "local-user",
        "scope" : "user",
        "iat"   : now,
        "exp"   : now + ttl_sec,
        "jti"   : secrets.token_urlsafe(16)
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def build_service_headers() -> dict[str, str]:
    """构建远端服务请求使用的签名头。"""
    encode_part: typing.Callable[[bytes], str] = (
        lambda value: base64.b64encode(value).decode().rstrip("=")
    )
    now = int(time.time())
    header = {
        "alg": "HS256",
        "typ": "JWT",
    }
    payload = {
        "app": const.APP_DESC,
        "iat": now,
        "exp": now + 300,
        "jti": secrets.token_hex(8),
    }
    header_part = encode_part(
        json.dumps(header, separators=(",", ":")).encode()
    )
    payload_part = encode_part(
        json.dumps(payload, separators=(",", ":")).encode()
    )
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(
        const.SHARED_SECRET.encode(),
        signing_input,
        hashlib.sha256,
    ).digest()

    return {
        "User-Agent": f"{const.APP_DESC}@{const.APP_VERSION}",
        "Content-Type": "application/json",
        "X-App-ID": const.PUBLISHER,
        "X-App-Token": (
            f"{header_part}.{payload_part}.{encode_part(signature)}"
        ),
        "X-App-Region": "Global",
        "X-App-Version": f"v{const.APP_VERSION}",
    }


def build_service_query() -> dict[str, str | int]:
    """构建远端服务请求使用的公共查询参数。"""
    return {
        "a": const.APP_DESC,
        "t": int(time.time()),
        "n": secrets.token_hex(8),
    }


if __name__ == '__main__':
    pass
