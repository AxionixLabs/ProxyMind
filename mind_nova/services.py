# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sqlite3
from backend.utilities.storage.services import (
    load_service_config, normalize_domain
)
from mind_nova import const


def service_domain() -> str:
    """返回当前远端服务域名；未配置时使用原有常量。"""
    try:
        domain = normalize_domain(load_service_config().get("domain"))
    except (OSError, sqlite3.Error):
        domain = ""

    return domain or const.DOMAIN.rstrip("/")


def endpoint(path: str) -> str:
    """基于当前远端服务域名拼接完整 URL。"""
    return f"{service_domain()}/{str(path or '').lstrip('/')}"


if __name__ == '__main__':
    pass
