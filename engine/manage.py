# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import json
import time
import httpx
import typing
import asyncio
import subprocess
import contextlib
from urllib.parse import urlparse
from loguru import logger
from mcp import types as mcp_types
from engine.tinker import MindError
from mind_core.design import Design
from mind_nova import (
    authentic, const, request, craft
)

class ServerManage(object):
    """管理本地后台服务的启动、探测、重启和关闭。"""

    def __init__(
        self,
        cmd: list[str],
        timeout: float = 0.6,
        env: typing.Optional[dict[str, str]] = None
    ):
        """保存启动命令并初始化本地服务 HTTP 客户端。"""
        self.cmd = cmd
        self.env = dict(env or {})
        self.url = const.BASE_URL.rstrip("/")

        parsed    = urlparse(self.url)
        self.port = int(parsed.port or 80)

        self._client = httpx.AsyncClient(
            base_url=self.url, timeout=timeout, trust_env=False
        )

        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    @staticmethod
    def has_new(local: dict[str, typing.Any], remote: dict[str, typing.Any]) -> bool:
        """比较本地和远端版本，判断是否存在更新。"""

        def parse_version(text: str, width: int = 3) -> tuple[int, ...]:
            """把版本字符串转换为固定宽度的整数元组。"""
            text = (text or "").strip().lower()
            if text.startswith("v"):
                text = text[1:]

            parts: list[int] = []
            for item in text.split("."):
                try:
                    parts.append(int(item))
                except ValueError:
                    parts.append(0)

            if len(parts) < width:
                parts.extend([0] * (width - len(parts)))

            return tuple(parts[:width])

        lv = parse_version(str(local.get("version") or "0"))
        rv = parse_version(str(remote.get("version") or "0"))

        return rv > lv

    async def check_update(self) -> None:
        """检查远端清单并在发现新版本时展示更新提示。"""
        if not (local := await self.probe_version()):
            return None

        if not (remote := await request.fetch_manifest()):
            return None

        if self.has_new(local, remote):
            Design.notify_update(local, remote)
        else:
            logger.debug(
                f"[Version] up to date: "
                f"local=v{local.get('version') or '-'} remote=v{remote.get('version') or '-'}"
            )

    async def probe_version(self) -> typing.Optional[dict[str, typing.Any]]:
        """读取本地服务版本信息；不可用时返回空值。"""
        headers = {"accept": "application/json"}

        try:
            resp = await self._client.request("GET", "/version", headers=headers)
            resp.raise_for_status()
            data = resp.json()

        except Exception as e:
            return logger.debug(f"[Version] probe failed: {type(e).__name__}: {e}")

        if not isinstance(data, dict) or not data.get("ok"):
            return None

        return data

    async def probe_healthz(self) -> bool:
        """检查本地服务健康端点是否返回有效状态。"""
        headers = {"accept": "application/json"}

        try:
            resp = await self._client.request("GET", "/healthz", headers=headers)

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            logger.debug(f"[Healthz] net timeout: {type(e).__name__}: {e}")
            return False
        except httpx.ConnectError as e:
            logger.debug(f"[Healthz] net connect: {type(e).__name__}: {e}")
            return False
        except httpx.RemoteProtocolError as e:
            logger.debug(f"[Healthz] remote protocol error: {e}")
            return False
        except httpx.HTTPError as e:
            logger.debug(f"[Healthz] httpx error: {type(e).__name__}: {e}")
            return False

        if resp.status_code >= 400:
            logger.debug(f"[Healthz] bad status: {resp.status_code}")
            return False

        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" not in ct:
            logger.debug(f"[Healthz] unexpected content-type: {ct!r}")
            return False

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[Healthz] json decode failed: {type(e).__name__}: {e}")
            return False

        logger.debug(f"[Healthz] {data}")

        return bool(data.get("ok")) and (data.get("service") == "helix mcp")

    async def probe_mcp_bootstrap(self) -> bool:
        """用最小 MCP 握手验证执行面是否真的可用。"""
        protocol_version = str(mcp_types.LATEST_PROTOCOL_VERSION)

        session_id: typing.Optional[str] = None

        headers = {
            "accept"        : "application/json",
            "content-type"  : "application/json",
            "authorization" : f"Bearer {authentic.manufacture_token()}"
        }
        initialize_payload = {
            "jsonrpc" : "2.0",
            "id"      : "bootstrap-init",
            "method"  : "initialize",
            "params"  : {
                "protocolVersion" : protocol_version,
                "capabilities"    : {},
                "clientInfo"      : {
                    "name"    : const.APP_DESC.lower(),
                    "version" : const.APP_VERSION
                }
            }
        }
        list_tools_payload = {
            "jsonrpc" : "2.0",
            "id"      : "bootstrap-tools",
            "method"  : "tools/list",
            "params"  : {}
        }

        try:
            init_resp = await self._client.request(
                "POST",
                const.MCP_ED,
                headers=headers,
                json=initialize_payload,
                timeout=3.0
            )
            init_body = init_resp.text

            if init_resp.status_code >= 400:
                logger.debug(
                    f"[MCP Probe] initialize bad status={init_resp.status_code} body={init_body[:240]!r}"
                )
                return False

            session_id = str(init_resp.headers.get("mcp-session-id") or "").strip() or None
            if not session_id:
                logger.debug("[MCP Probe] initialize missing session id")
                return False

            list_resp = await self._client.request(
                "POST",
                const.MCP_ED,
                headers={
                    **headers,
                    "mcp-session-id"       : session_id,
                    "mcp-protocol-version" : protocol_version
                },
                json=list_tools_payload,
                timeout=3.0
            )
            list_body = list_resp.text

            if list_resp.status_code >= 400:
                logger.debug(
                    f"[MCP Probe] tools/list bad status={list_resp.status_code} body={list_body[:240]!r}"
                )
                return False

            try:
                payload = list_resp.json()
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"[MCP Probe] tools/list json decode failed: {type(e).__name__}: {e}")
                return False

            result = payload.get("result") if isinstance(payload, dict) else None
            tools  = result.get("tools") if isinstance(result, dict) else None
            ok     = isinstance(tools, list)

            logger.debug(
                f"[MCP Probe] ok={ok} session_id={'set' if session_id else 'missing'} "
                f"tool_count={len(tools) if isinstance(tools, list) else '-'}"
            )

            return ok

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            logger.debug(f"[MCP Probe] net timeout: {type(e).__name__}: {e}")
            return False
        except httpx.ConnectError as e:
            logger.debug(f"[MCP Probe] net connect: {type(e).__name__}: {e}")
            return False
        except httpx.RemoteProtocolError as e:
            logger.debug(f"[MCP Probe] remote protocol error: {e}")
            return False
        except httpx.HTTPError as e:
            logger.debug(f"[MCP Probe] httpx error: {type(e).__name__}: {e}")
            return False

        finally:
            if session_id:
                with contextlib.suppress(Exception):
                    await self._client.request(
                        "DELETE",
                        const.MCP_ED,
                        headers={
                            "accept"               : "application/json",
                            "authorization"        : f"Bearer {authentic.manufacture_token()}",
                            "mcp-session-id"       : session_id,
                            "mcp-protocol-version" : str(mcp_types.LATEST_PROTOCOL_VERSION)
                        },
                        timeout=1.5
                    )

    async def wait_until_ready(self, wait_sec: float, interval: float) -> bool:
        """在限定时间内轮询健康检查和 MCP 握手状态。"""
        deadline = time.monotonic() + max(0.5, wait_sec)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)

            if not await self.probe_healthz():
                continue
            if not await self.probe_mcp_bootstrap():
                continue

            logger.debug(
                f"SYNC ▸ {const.APP_DESC} MCP neural core online."
            )
            return True

        return False

    async def restart_unlocked(self) -> None:
        """重启本地后台服务；调用方负责持有生命周期锁。"""
        logger.debug(f"[Server] restarting local service on port {self.port}")
        with contextlib.suppress(Exception):
            await craft.kill_port(self.port)
        await asyncio.sleep(0.2)
        await self.spawn()

    async def restart(self) -> None:
        """串行重启本地后台服务。"""
        async with self._lifecycle_lock:
            await self.restart_unlocked()

    async def ensure_running_unlocked(self, wait_sec: float = 10.0, interval: float = 0.3) -> None:
        """确保本地后台服务可用；调用方负责持有生命周期锁。"""
        if await self.probe_healthz() and await self.probe_mcp_bootstrap():
            return None

        await self.spawn()

        if await self.wait_until_ready(wait_sec, interval):
            return await self.check_update()

        logger.debug("[Server] initial start did not pass MCP bootstrap probe, forcing restart")
        await self.restart_unlocked()

        if await self.wait_until_ready(wait_sec, interval):
            return await self.check_update()

        raise MindError("MCP not ready (bootstrap timeout)")

    async def ensure_running(self, wait_sec: float = 10.0, interval: float = 0.3) -> None:
        """串行确保本地后台服务可用。"""
        async with self._lifecycle_lock:
            await self.ensure_running_unlocked(wait_sec=wait_sec, interval=interval)

    async def spawn(self) -> None:
        """按配置命令启动本地后台服务进程。"""
        kwargs: dict[str, typing.Any] = {
            "stdin"  : asyncio.subprocess.DEVNULL,
            "stdout" : asyncio.subprocess.DEVNULL,
            "stderr" : asyncio.subprocess.DEVNULL
        }
        if self.env:
            kwargs["env"] = {**os.environ, **self.env}

        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            await asyncio.create_subprocess_exec(*self.cmd, **kwargs)
        except Exception as e:
            raise MindError(f"Spawn failed: {type(e).__name__}: {e}") from e

    async def close(self) -> None:
        """关闭本地服务管理器持有的 HTTP 客户端。"""
        await self._client.aclose()


if __name__ == '__main__':
    pass
