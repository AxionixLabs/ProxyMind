# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextvars
import typing
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)

Client = typing.TypeVar("Client")


@asynccontextmanager
async def open_http_client(
    factory: typing.Callable[[], AbstractAsyncContextManager[Client]],
) -> typing.AsyncIterator[Client]:
    """在线程中构造请求客户端，在调用方事件循环中使用并关闭。

    工厂只构造未连接的客户端，不启动异步任务。当前请求持有整个生命周期；
    构造期间取消仍须回收线程返回的客户端，且不得继续发送请求。
    客户端构造不再执行阻塞 IO 后可移除此适配。
    """
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    # TLS 证书和代理初始化包含同步 IO；线程工作不作为可取消的异步任务遗弃。
    creation = loop.run_in_executor(None, context.run, factory)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            client_context = await asyncio.shield(creation)
            break
        except asyncio.CancelledError as error:
            if creation.cancelled():
                raise
            cancellation = error
        except Exception as error:
            if cancellation is not None:
                raise cancellation from error
            raise

    async with client_context as client:
        if cancellation is None:
            yield client
    if cancellation is not None:
        raise cancellation


if __name__ == '__main__':
    pass
