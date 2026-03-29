# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import ipaddress
from urllib.parse import urlparse


class ClockService(object):

    @staticmethod
    def ms_now() -> int:
        """返回当前时间的毫秒级时间戳。"""
        return int(time.time() * 1000)

    @staticmethod
    def ms_since(t0: float) -> int:
        """返回从给定性能计时起点到现在的耗时毫秒数。"""
        return int((time.perf_counter() - t0) * 1000)


class UrlService(object):

    @staticmethod
    def join(base_url: typing.Optional[str], url: str) -> str:
        """把可选的 base_url 与相对 url 拼成最终访问地址。"""
        if not base_url:
            return url
        return base_url.rstrip("/") + "/" + url.lstrip("/")

    @staticmethod
    def is_loopback(url: typing.Optional[str]) -> bool:
        """判断 URL 是否指向本机回环地址，用于跳过环境代理。"""
        if not url:
            return False

        try:
            parsed = urlparse(str(url))
        except ValueError:
            return False

        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        if host == "localhost":
            return True

        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def httpx_client_kwargs(
        *,
        url: typing.Optional[str],
        timeout: float,
        follow_redirects: bool = True
    ) -> dict[str, typing.Any]:
        """为回环地址关闭环境代理，其余请求保持默认行为。"""
        return {
            "timeout"          : timeout,
            "follow_redirects" : follow_redirects,
            "trust_env"        : not UrlService.is_loopback(url)
        }


if __name__ == '__main__':
    pass
