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
from mcp import types as mcp_types
from engine.errors import AppError
from engine.observability import (
    observe,
    observe_exception
)
from engine.ports import terminate_port_process
from mind_nova.service_auth import manufacture_token
from mind_nova import const


class ServerManage(object):
    """管理本地后台服务的启动、探测、重启和关闭。"""

    def __init__(
        self,
        cmd: list[str],
        timeout: float = 0.6,
        env: typing.Optional[dict[str, str]] = None,
        cwd: str | os.PathLike[str] | None = None
    ):
        """保存启动命令并初始化本地服务 HTTP 客户端。"""
        self.cmd = cmd
        self.env = dict(env or {})
        self.cwd = os.fspath(cwd) if cwd is not None else None
        self.url = const.BASE_URL.rstrip("/")

        parsed    = urlparse(self.url)
        self.port = int(parsed.port or 80)

        self._client = httpx.AsyncClient(
            base_url=self.url, timeout=timeout, trust_env=False
        )

        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    async def probe_healthz(self) -> bool:
        """检查本地服务健康端点是否返回有效状态。"""
        headers = {"accept": "application/json"}

        try:
            resp = await self._client.request("GET", "/healthz", headers=headers)

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            observe_exception("health.probe.failed", e, level="WARNING", reason="timeout")
            return False
        except httpx.ConnectError as e:
            observe_exception("health.probe.failed", e, level="WARNING", reason="connect")
            return False
        except httpx.RemoteProtocolError as e:
            observe_exception("health.probe.failed", e, level="WARNING", reason="protocol")
            return False
        except httpx.HTTPError as e:
            observe_exception("health.probe.failed", e, level="WARNING", reason="http")
            return False

        if resp.status_code >= 400:
            observe("health.probe.failed", level="WARNING", reason="status", status=resp.status_code)
            return False

        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" not in ct:
            observe("health.probe.failed", level="WARNING", reason="content_type", content_type=ct)
            return False

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            observe_exception("health.probe.failed", e, level="WARNING", reason="decode")
            return False

        ok = bool(data.get("ok")) and (data.get("service") == "helix mcp")
        observe("health.probe.complete", ok=ok, status=resp.status_code)
        return ok

    async def probe_mcp_bootstrap(self) -> bool:
        """用最小 MCP 握手验证执行面是否真的可用。"""
        protocol_version = str(mcp_types.LATEST_PROTOCOL_VERSION)

        session_id: typing.Optional[str] = None

        headers = {
            "accept"        : "application/json",
            "content-type"  : "application/json",
            "authorization" : f"Bearer {manufacture_token()}"
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
            if init_resp.status_code >= 400:
                observe(
                    "mcp.probe.failed",
                    level="WARNING",
                    phase="initialize",
                    reason="status",
                    status=init_resp.status_code,
                )
                return False

            session_id = str(init_resp.headers.get("mcp-session-id") or "").strip() or None
            if not session_id:
                observe(
                    "mcp.probe.failed",
                    level="WARNING",
                    phase="initialize",
                    reason="missing_session",
                )
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
            if list_resp.status_code >= 400:
                observe(
                    "mcp.probe.failed",
                    level="WARNING",
                    phase="tools_list",
                    reason="status",
                    status=list_resp.status_code,
                )
                return False

            try:
                payload = list_resp.json()
            except (json.JSONDecodeError, ValueError) as e:
                observe_exception(
                    "mcp.probe.failed",
                    e,
                    level="WARNING",
                    phase="tools_list",
                    reason="decode",
                )
                return False

            result = payload.get("result") if isinstance(payload, dict) else None
            tools  = result.get("tools") if isinstance(result, dict) else None
            ok     = isinstance(tools, list)

            observe(
                "mcp.probe.complete",
                ok=ok,
                session=True,
                tool_count=len(tools) if isinstance(tools, list) else 0,
            )

            return ok

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            observe_exception("mcp.probe.failed", e, level="WARNING", reason="timeout")
            return False
        except httpx.ConnectError as e:
            observe_exception("mcp.probe.failed", e, level="WARNING", reason="connect")
            return False
        except httpx.RemoteProtocolError as e:
            observe_exception("mcp.probe.failed", e, level="WARNING", reason="protocol")
            return False
        except httpx.HTTPError as e:
            observe_exception("mcp.probe.failed", e, level="WARNING", reason="http")
            return False

        finally:
            if session_id:
                with contextlib.suppress(Exception):
                    await self._client.request(
                        "DELETE",
                        const.MCP_ED,
                        headers={
                            "accept"               : "application/json",
                            "authorization"        : f"Bearer {manufacture_token()}",
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

            observe("server.ready", port=self.port)
            return True

        return False

    async def restart_unlocked(self) -> None:
        """重启本地后台服务；调用方负责持有生命周期锁。"""
        observe("server.restart", port=self.port)
        with contextlib.suppress(Exception):
            await terminate_port_process(self.port)
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
            return None

        observe("server.restart.forced", level="WARNING", reason="bootstrap_probe")
        await self.restart_unlocked()

        if await self.wait_until_ready(wait_sec, interval):
            return None

        raise AppError("MCP not ready (bootstrap timeout)")

    async def ensure_running(self, wait_sec: float = 10.0, interval: float = 0.3) -> None:
        """串行确保本地后台服务可用。"""
        async with self._lifecycle_lock:
            await self.ensure_running_unlocked(wait_sec=wait_sec, interval=interval)

    async def spawn(self) -> None:
        """按配置命令启动本地后台服务进程。"""
        observe("server.spawn.start", port=self.port)

        kwargs: dict[str, typing.Any] = {
            "stdin"  : asyncio.subprocess.DEVNULL,
            "stdout" : asyncio.subprocess.DEVNULL,
            "stderr" : asyncio.subprocess.DEVNULL
        }

        if self.env:
            kwargs["env"] = {**os.environ, **self.env}
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd

        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            process = await asyncio.create_subprocess_exec(*self.cmd, **kwargs)
        except Exception as e:
            observe_exception("server.spawn.failed", e, port=self.port)
            raise AppError(f"Spawn failed: {type(e).__name__}: {e}") from e

        observe("server.spawn.complete", port=self.port, pid=process.pid)

    async def close(self) -> None:
        """关闭本地服务管理器持有的 HTTP 客户端。"""
        await self._client.aclose()


if __name__ == '__main__':
    pass
