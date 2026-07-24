# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
import contextlib
from engine.observability import observe_exception
from mind_nova import const

if typing.TYPE_CHECKING:
    from engine.manage import ServerManage


def _should_raise(exc: BaseException) -> bool:
    """判断后台保活是否应向外传播中断类异常。"""
    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))


async def _recover_local_service(server_manager: "ServerManage | None") -> None:
    """尽力恢复本地后台服务；恢复失败只记录，不中断保活任务。"""
    if server_manager is None:
        return None

    try:
        await server_manager.ensure_running()
    except BaseException as exc:
        if _should_raise(exc):
            raise
        observe_exception("keepalive.recovery.failed", exc, level="WARNING")


async def run_keepalive(
    stop_event: asyncio.Event,
    *,
    req_client: httpx.AsyncClient | None = None,
    server_manager: "ServerManage | None" = None
) -> None:
    """后台定时执行 keepalive，并接受服务端返回的动态周期。"""
    keepalive_sec = float(const.KEEPALIVE_SEC)
    owns_client   = req_client is None

    client = req_client or httpx.AsyncClient(
        timeout=httpx.Timeout(float(const.KEEPALIVE_TIMEOUT_SEC), connect=1.5)
    )

    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=keepalive_sec)
                break
            except asyncio.TimeoutError:
                pass

            try:
                response = await client.get(
                    f"{const.BASE_URL}/api/keepalive",
                    headers={"accept": "application/json"},
                    timeout=float(const.KEEPALIVE_TIMEOUT_SEC),
                )
                response.raise_for_status()

                payload = (
                    response.json()
                    if response.headers.get("content-type", "").lower().startswith("application/json")
                    else {}
                )
                if isinstance(payload, dict):
                    value = payload.get("keepalive_sec")
                    if isinstance(value, (int, float)) and value > 0:
                        keepalive_sec = float(value)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                observe_exception("keepalive.probe.failed", exc, level="WARNING")
                await _recover_local_service(server_manager)

    finally:
        if owns_client:
            with contextlib.suppress(Exception):
                await client.aclose()


if __name__ == '__main__':
    pass
