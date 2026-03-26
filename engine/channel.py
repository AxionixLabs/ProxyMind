# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hmac
import json
import time
import httpx
import base64
import typing
import hashlib
import secrets
from engine.tinker import MindError
from mind_nova import const


class Channel(object):
    """Channel class."""

    @staticmethod
    def make_headers() -> dict[str, typing.Any]:
        """构建应用请求所需的标准化 HTTP 请求头，包含基于 HMAC-SHA256 的 JWT 样式认证令牌。"""
        b64_enc: typing.Callable[[str], str] = lambda x: base64.b64encode(x).decode().rstrip("=")

        header = {
            "alg" : "HS256",
            "typ" : "JWT"
        }
        payload = {
            "app" : const.APP_DESC,
            "iat" : int(time.time()),
            "exp" : int(time.time()) + 300,
            "jti" : secrets.token_hex(8)
        }

        header_bytes  = b64_enc(json.dumps(header, separators=(",", ":")).encode())
        payload_bytes = b64_enc(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_bytes}.{payload_bytes}".encode()
        sig           = hmac.new(const.SHARED_SECRET.encode(), signing_input, hashlib.sha256).digest()

        return {
            "User-Agent"    : f"{const.APP_DESC}@{const.APP_VERSION}",
            "Content-Type"  : f"application/json",
            "X-App-ID"      : const.PUBLISHER,
            "X-App-Token"   : f"{header_bytes}.{payload_bytes}.{b64_enc(sig)}",
            "X-App-Region"  : f"Global",
            "X-App-Version" : f"v{const.APP_VERSION}"
        }

    @staticmethod
    def make_params() -> dict[str, typing.Any]:
        """构造通信参数集合。"""
        return {
            "a": const.APP_DESC, "t": int(time.time()), "n": secrets.token_hex(8)
        }


class Messenger(object):
    """Messenger class."""

    def __init__(self):
        self.client: typing.Optional["httpx.AsyncClient"] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=10)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.client:
            await self.client.aclose()

    async def poke(self, method: str, url: typing.Union["httpx.URL", str], *args, **kwargs) -> "httpx.Response":
        """发送异步 HTTP 请求，并统一封装异常处理。"""
        assert self.client, f"Client instance is missing. Did you forget to initialize it?"

        try:
            response = await self.client.request(method, url, *args, **kwargs)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            raise MindError(f"❌ {e.response.status_code} -> {e.response.text}")
        except httpx.HTTPError as e:
            raise MindError(f"❌ {e}")


if __name__ == '__main__':
    pass
