# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from urllib.parse import urlparse
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
    """通过本地 Helix HTTP API 读取远端服务配置。"""

    @property
    def service_config_api(self) -> str:
        """返回本地服务配置接口地址。"""
        return const.BASE_URL.rstrip("/") + "/api/service-config"

    async def load_domain(self) -> str:
        """读取已配置的远端服务域名；失败或未配置时返回空字符串。"""
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


if __name__ == '__main__':
    pass
