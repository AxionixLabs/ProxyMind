# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from urllib.parse import urlparse

import httpx

from observability import (
    observe,
    observe_exception
)
from protocol.transport import config


def normalize_domain(value: typing.Any) -> str:
    """规范化服务域名；空值或非法值返回空字符串。"""
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        return ""

    parsed = urlparse(domain)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return domain


class ConfigReader(typing.Protocol):
    """定义服务配置读取所需的最小配置端口。"""

    def load(self, *, create: bool = True) -> dict[str, typing.Any]:
        """读取当前有效配置快照。"""


class ServiceConfig(object):
    """管理服务域名配置的读取。"""

    def __init__(
        self,
        config_reader: ConfigReader,
    ) -> None:
        """初始化配置来源。"""
        self._config_reader = config_reader

    @property
    def service_config_api(self) -> str:
        """返回服务配置接口地址。"""
        return config.BASE_URL.rstrip("/") + "/api/service-config"

    def load_local_domain(self) -> str:
        """读取本地配置中的服务域名。"""
        try:
            config = self._config_reader.load()
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "service_domain.local.failed",
                error,
                level="WARNING",
                phase="config",
            )
            return ""

        service = config.get("service") if isinstance(config, dict) else None
        if not isinstance(service, dict):
            return ""

        return normalize_domain(service.get("domain"))

    async def load_remote_domain(self) -> str:
        """读取配置接口中的服务域名。"""
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                resp = await client.get(self.service_config_api)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, TypeError, ValueError) as error:
            observe_exception(
                "service_domain.remote.failed",
                error,
                level="WARNING",
            )
            return ""

        if not isinstance(payload, dict):
            return ""

        data = payload.get("data")
        if not isinstance(data, dict):
            return ""

        return normalize_domain(data.get("domain"))

    async def load_domain(self) -> str:
        """读取本地服务域名配置。"""
        domain = self.load_local_domain()
        observe(
            "service_domain.loaded",
            source="local" if domain else "default",
            configured=bool(domain),
        )
        return domain


if __name__ == '__main__':
    pass
