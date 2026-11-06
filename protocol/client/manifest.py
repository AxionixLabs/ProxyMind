# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import platform
import sys

import httpx

from observability import observe_exception
from protocol.schema.runtime_manifest import (
    RuntimeManifest,
    RuntimeManifestResponse,
    RuntimeService,
    RuntimeStation,
)
from protocol.transport.auth import (
    build_service_headers,
    build_service_query,
)
from protocol.transport.endpoints import service_endpoints
from protocol.transport.http_client import open_http_client


async def fetch_manifest(
    service: RuntimeService,
    *,
    station: RuntimeStation | None = None,
    arch: str | None = None,
) -> RuntimeManifest | None:
    """获取并校验独立组件清单，仅网络暂不可用时返回空值。"""
    selected_station = station or sys.platform
    if selected_station not in {"win32", "darwin"}:
        raise ValueError("Runtime platform is unavailable")
    machine = (arch or platform.machine()).lower()
    selected_arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    headers = build_service_headers()
    params = build_service_query() | {
        "station": selected_station,
        "arch": selected_arch,
        "service": service,
    }

    try:
        async with open_http_client(lambda: httpx.AsyncClient(timeout=10.0)) as client:
            resp = await client.request(
                "GET",
                service_endpoints.endpoint("/mind-manifest"),
                headers=headers,
                params=params
            )
            resp.raise_for_status()
            result = RuntimeManifestResponse.model_validate_json(resp.content).data

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.TransportError as error:
        observe_exception("manifest.fetch.failed", error, level="WARNING")
        return None
    except httpx.HTTPStatusError as error:
        if error.response.status_code < 500:
            raise ValueError(f"Runtime manifest rejected: HTTP {error.response.status_code}") from error
        observe_exception("manifest.fetch.failed", error, level="WARNING")
        return None
    if (result.service != service or result.station != selected_station
            or result.arch != selected_arch or result.host != params["a"]):
        raise ValueError("Runtime manifest does not match the requested component and platform")
    return result


if __name__ == '__main__':
    pass
