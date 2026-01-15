#    ____ _                            _
#   / ___| |__   __ _ _ __  _ __   ___| |
#  | |   | '_ \ / _` | '_ \| '_ \ / _ \ |
#  | |___| | | | (_| | | | | | | |  __/ |
#   \____|_| |_|\__,_|_| |_|_| |_|\___|_|
#

import hmac
import json
import time
import httpx
import base64
import typing
import hashlib
import secrets
from engine.tinker import MindError
from mindnova import const


class Channel(object):
    """
    参数通道生成器，用于构建基础通信参数集。
    """

    @staticmethod
    def make_headers() -> dict[str, typing.Any]:
        """
        构建应用请求所需的标准化 HTTP 请求头，包含基于 HMAC-SHA256 的 JWT 样式认证令牌。

        该方法主要生成一组带有自签名的安全头部字段，用于与服务端进行安全通信。
        返回的请求头包含版本、区域、身份标识以及签名令牌等关键信息。

        Returns
        -------
        dict[str, typing.Any]
            包含以下字段的请求头字典：

            - "User-Agent" : str
              应用标识与版本号，格式为 <应用描述>@<版本号>。

            - "Content-Type" : str
              固定值 "application/json"，表示请求体为 JSON 格式。

            - "X-App-ID" : str
              应用发布者的唯一标识，用于服务端识别调用来源。

            - "X-App-Token" : str
              自生成的签名令牌，遵循类似 JWT 的结构。

            - "X-App-Region" : str
              固定值 "Global"，表示应用部署区域。

            - "X-App-Version" : str
              应用版本号，格式为 "v<版本号>"。

        Notes
        -----
        - 签名算法采用 HMAC-SHA256，基于预置的共享密钥保证令牌防篡改。
        - 有效期固定为 5 分钟（300 秒），超时后需重新生成请求头。
        - 该请求头设计可与服务端共享密钥机制配合，用于轻量级的安全认证流程。
        """
        b64_enc: typing.Callable[[str], str] = lambda x: base64.b64encode(x).decode().rstrip("=")

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "app": const.APP_DESC,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "jti": secrets.token_hex(8)
        }

        header_bytes = b64_enc(json.dumps(header, separators=(",", ":")).encode())
        payload_bytes = b64_enc(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_bytes}.{payload_bytes}".encode()
        sig = hmac.new(const.SHARED_SECRET.encode(), signing_input, hashlib.sha256).digest()

        return {
            "User-Agent": f"{const.APP_DESC}@{const.APP_VERSION}",
            "Content-Type": f"application/json",
            "X-App-ID": const.PUBLISHER,
            "X-App-Token": f"{header_bytes}.{payload_bytes}.{b64_enc(sig)}",
            "X-App-Region": f"Global",
            "X-App-Version": f"v{const.APP_VERSION}"
        }

    @staticmethod
    def make_params() -> dict[str, typing.Any]:
        """
        构造通信参数集合。

        Returns
        -------
        dict[str, typing.Any]
            包含默认参数的字典，字段包括：
            - `a`: 应用描述常量
            - `t`: 当前时间戳（秒）
            - `n`: 随机生成的16位十六进制字符串
        """
        return {
            "a": const.APP_DESC, "t": int(time.time()), "n": secrets.token_hex(8)
        }


class Messenger(object):
    """
    异步通信信使，封装 HTTP 请求发送与连接管理。

    Attributes
    ----------
    client : httpx.AsyncClient | None
        异步 HTTP 客户端，生命周期受上下文管理控制。
    """

    def __init__(self):
        """
        初始化 Messenger 实例，默认未建立客户端连接。
        """
        self.client: typing.Optional["httpx.AsyncClient"] = None

    async def __aenter__(self):
        """
        异步上下文进入函数，初始化 HTTP 客户端。

        Returns
        -------
        Messenger
            当前实例自身，可用于发送异步请求。
        """
        self.client = httpx.AsyncClient(timeout=10)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """
        异步上下文退出函数，自动关闭客户端连接。
        """
        if self.client:
            await self.client.aclose()

    async def poke(self, method: str, url: typing.Union["httpx.URL", str], *args, **kwargs) -> "httpx.Response":
        """
        发送异步 HTTP 请求，并统一封装异常处理。

        Parameters
        ----------
        method : str
            请求方法，如 "GET"、"POST" 等。

        url : URL | str
            请求地址，可为字符串或 URL 对象。

        *args : Any
            位置参数，传递给 httpx。

        **kwargs : Any
            关键字参数，传递给 httpx。

        Returns
        -------
        httpx.Response
            请求响应对象，需调用者手动解析。

        Raises
        ------
        MindError
            请求失败或连接异常时抛出。
        """
        assert self.client, f"Client instance is missing. Did you forget to initialize it?"

        try:
            response = await self.client.request(method, url, *args, **kwargs)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            raise MindError(f"❌ {e.response.status_code} -> {e.response.text}")
        except httpx.HTTPError as e:
            raise MindError(f"❌ {e}")


if __name__ == '__main__':
    pass
