# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import secrets
import typing
from contextlib import asynccontextmanager
from dataclasses import (
    dataclass,
    field,
)
from urllib.parse import (
    parse_qsl,
    urlsplit,
)

from agent.domain.mcp_oauth import (
    McpOAuthError,
    McpOAuthErrorCode,
)
from metadata import const


@dataclass(frozen=True)
class _CallbackResult:
    """保存只在当前登录中消费一次的授权码或固定错误。"""

    code: str | None = field(default=None, repr=False)
    error: McpOAuthErrorCode | None = None


class OAuthCallbackReceiver:
    """拥有一次登录的 loopback socket、接入任务及一次性结果，不记录请求 URL。"""

    def __init__(self, state: str, issuer: str, require_issuer: bool) -> None:
        """冻结授权绑定，连接和结果只在创建它们的事件循环中使用。"""
        self._state = state
        self._issuer = issuer
        self._require_issuer = require_issuer
        self._result: asyncio.Future[_CallbackResult] = asyncio.get_running_loop().create_future()
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None
        self.redirect_uri = ""
        self._host = ""

    async def start(self, port: int | None) -> None:
        """先绑定 IPv4 loopback，再按实际端口生成重定向地址。"""
        try:
            self._server = await asyncio.start_server(
                self._connected, host="127.0.0.1", port=port or 0,
                limit=8192, reuse_address=False,
            )
        except OSError:
            raise McpOAuthError("callback_unavailable") from None
        actual_port = self._server.sockets[0].getsockname()[1]
        if not isinstance(actual_port, int):
            raise McpOAuthError("callback_unavailable")
        self._host = f"127.0.0.1:{actual_port}"
        self.redirect_uri = f"http://{self._host}/callback"

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """登记短期连接，限制未完成请求的数量。"""
        if len(self._tasks) >= 16:
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _accept(self, header: bytes) -> tuple[int, str]:
        """验证路由、Host、state 和 issuer；错误路由不能消费等待中的授权。"""
        lines = header.decode("ascii").split("\r\n")
        method, raw_target, version = lines[0].split(" ")
        if method != "GET" or version not in ("HTTP/1.0", "HTTP/1.1"):
            return 405, "Unsupported request."
        hosts = [line.partition(":")[2].strip() for line in lines[1:] if line.partition(":")[0].lower() == "host"]
        if hosts != [self._host]:
            return 400, "Invalid request."
        target = urlsplit(raw_target)
        if target.scheme or target.netloc or target.fragment or target.path != "/callback":
            return 404, "Unknown callback."
        pairs = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16)
        parameters = dict(pairs)
        if len(parameters) != len(pairs):
            return 400, "Invalid request."
        state = parameters.get("state", "")
        if not state.isascii() or not secrets.compare_digest(state, self._state):
            return 400, "Invalid authorization state."
        if self._result.done():
            return 409, "Authorization already received."
        issuer = parameters.get("iss")
        if (issuer is not None and issuer != self._issuer) or (self._require_issuer and issuer is None):
            self._result.set_result(_CallbackResult(error="invalid_response"))
            return 400, "Invalid authorization issuer."
        code = parameters.get("code")
        error = parameters.get("error")
        if error is not None and code is not None:
            self._result.set_result(_CallbackResult(error="invalid_response"))
            return 400, "Invalid authorization response."
        if error is not None:
            self._result.set_result(_CallbackResult(error="authorization_denied" if error == "access_denied" else "invalid_response"))
            return 200, "Authorization failed. Return to the terminal."
        if not code or any(ord(char) < 32 for char in code):
            self._result.set_result(_CallbackResult(error="invalid_response"))
            return 400, "Missing authorization code."
        self._result.set_result(_CallbackResult(code=code))
        return 200, "Authorization received. Return to the terminal to finish signing in."

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """处理有界 HTTP 请求并关闭连接，不在响应中回显身份或错误正文。"""
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b"\r\n\r\n")
                try:
                    status, message = self._accept(header)
                except (ValueError, UnicodeError):
                    status, message = 400, "Invalid request."
                body = f"{const.APP_DESC}: {message}\n".encode(const.CHARSET)
                response = (
                    f"HTTP/1.1 {status} Response\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                    "Referrer-Policy: no-referrer\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(response + body)
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            self._writers.discard(writer)

    async def wait(self) -> str:
        """返回一次性授权码；外层用例负责总期限和用户取消。"""
        result = await self._result
        if result.error is not None:
            raise McpOAuthError(result.error)
        if result.code is None:
            raise McpOAuthError("invalid_response")
        return result.code

    async def close(self) -> None:
        """停止接入并收束所有连接任务，使失败和取消不会遗留回调端口。"""
        if self._server is not None:
            self._server.close()
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        if not self._result.done():
            self._result.cancel()


@asynccontextmanager
async def oauth_callback(
    *, port: int | None, state: str, issuer: str, require_issuer: bool,
) -> typing.AsyncIterator[OAuthCallbackReceiver]:
    """把监听、授权结果和所有连接限定在单次显式登录的生命周期内。"""
    receiver = OAuthCallbackReceiver(state, issuer, require_issuer)
    try:
        await receiver.start(port)
        yield receiver
    finally:
        await receiver.close()


if __name__ == '__main__':
    pass
