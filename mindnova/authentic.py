# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import jwt
import hmac
import time
import base64
import hashlib
import secrets
from mindnova import const


def derive_hs256_secret(*, step_sec: int = 300, ts: int | None = None) -> str:
    """
    step_sec=300 -> 5分钟轮换一次
    返回 base64url 的派生 secret（字符串）
    """
    if ts is None: ts = int(time.time())

    bucket = ts // step_sec

    msg = str(bucket).encode(const.CHARSET)
    key = const.MASTER.encode(const.CHARSET)

    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode(const.CHARSET).rstrip("=")


def manufacture_token(ttl_sec: int = 3600) -> str:
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


if __name__ == '__main__':
    pass
