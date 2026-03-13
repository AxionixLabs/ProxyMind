#   ____                 _   _      _
#  / ___|___  _ __ ___  | | | | ___| |_ __   ___ _ __ ___
# | |   / _ \| '__/ _ \ | |_| |/ _ \ | '_ \ / _ \ '__/ __|
# | |__| (_) | | |  __/ |  _  |  __/ | |_) |  __/ |  \__ \
#  \____\___/|_|  \___| |_| |_|\___|_| .__/ \___|_|  |___/
#                                    |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing


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


if __name__ == '__main__':
    pass
