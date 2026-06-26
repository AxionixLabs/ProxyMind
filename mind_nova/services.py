# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from urllib.parse import urlparse
from mind_nova import const

SERVICE_DOMAIN_ENV = "MIND_SERVICE_DOMAIN"


def normalize_domain(value: typing.Any) -> str:
    """规范化服务域名；空值或非法值返回空字符串。"""
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        return ""

    parsed = urlparse(domain)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return domain


class ServiceEndpoints(object):
    """远端服务 endpoint 解析器。"""

    def __init__(
        self,
        *,
        default_domain: str,
        env_name: str = SERVICE_DOMAIN_ENV
    ) -> None:
        self.default_domain = normalize_domain(default_domain)
        self.env_name       = str(env_name or SERVICE_DOMAIN_ENV)
        self.configured     = ""

    def configure(self, domain: typing.Any) -> str:
        """配置运行时远端服务域名；非法或空值会清空覆盖。"""
        self.configured = normalize_domain(domain)
        return self.configured

    def domain(self) -> str:
        """返回当前远端服务域名。"""
        return (
            self.configured
            or normalize_domain(os.environ.get(self.env_name))
            or self.default_domain
            or const.DOMAIN.rstrip("/")
        )

    def endpoint(self, path: typing.Any) -> str:
        """基于当前远端服务域名拼接完整 URL。"""
        return f"{self.domain()}/{str(path or '').lstrip('/')}"


service_endpoints = ServiceEndpoints(default_domain=const.DOMAIN)


if __name__ == '__main__':
    pass
