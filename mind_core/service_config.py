# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import tomllib
from urllib.parse import urlparse
from pathlib import Path
from mind_core.config import (
    default_config_path,
    ensure_config,
    load_config,
    normalize_config
)
from mind_nova import const


def normalize_domain(value: typing.Any) -> str:
    """规范化服务域名；空值或非法值返回空字符串。"""
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        return ""

    parsed = urlparse(domain)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return domain


class ServiceConfig(object):
    """管理服务域名配置的读取。"""

    def __init__(
        self,
        config_file: typing.Any | None = None
    ) -> None:
        """初始化配置文件路径。"""
        self.config_file = config_file or default_config_path()

    @property
    def service_config_api(self) -> str:
        """返回服务配置接口地址。"""
        return const.BASE_URL.rstrip("/") + "/api/service-config"

    def load_local_domain(self) -> str:
        """读取本地配置中的服务域名。"""
        target = ensure_config(self.config_file)

        try:
            with Path(target).expanduser().open("rb") as file:
                raw_config = tomllib.load(file)
        except (OSError, TypeError, ValueError):
            return ""

        raw_service = raw_config.get("service") if isinstance(raw_config, dict) else None
        if isinstance(raw_service, dict) and "domain" in raw_service:
            return normalize_domain(raw_service.get("domain"))

        try:
            config = normalize_config(load_config(target))
        except (OSError, TypeError, ValueError):
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
        except (httpx.HTTPError, TypeError, ValueError):
            return ""

        if not isinstance(payload, dict):
            return ""

        data = payload.get("data")
        if not isinstance(data, dict):
            return ""

        return normalize_domain(data.get("domain"))

    async def load_domain(self) -> str:
        """读取本地服务域名配置。"""
        return self.load_local_domain()


if __name__ == '__main__':
    pass
