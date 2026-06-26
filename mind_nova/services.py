# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from urllib.parse import urlparse
from mind_nova import const

SERVICE_DOMAIN_ENV = "MIND_SERVICE_DOMAIN"


class _ServiceDomainState(object):
    """保存运行期远端服务域名覆盖。"""

    def __init__(self) -> None:
        self.domain: str = ""


_state = _ServiceDomainState()


def configure_service_domain(domain: typing.Any) -> str:
    """配置远端服务域名；非法或空值会清空运行时覆盖。"""
    _state.domain = normalize_domain(domain)
    return _state.domain


def normalize_domain(value: typing.Any) -> str:
    """规范化服务域名；空值或非法值返回空字符串。"""
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        return ""

    parsed = urlparse(domain)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return domain


def service_domain() -> str:
    """返回当前远端服务域名；未配置时使用原有常量。"""
    domain = _state.domain or normalize_domain(os.environ.get(SERVICE_DOMAIN_ENV))

    return domain or const.DOMAIN.rstrip("/")


def endpoint(path: str) -> str:
    """基于当前远端服务域名拼接完整 URL。"""
    return f"{service_domain()}/{str(path or '').lstrip('/')}"


if __name__ == '__main__':
    pass
