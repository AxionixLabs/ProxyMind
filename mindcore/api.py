#      _          _
#     / \   _ __ (_)
#    / _ \ | '_ \| |
#   / ___ \| |_) | |
#  /_/   \_\ .__/|_|
#          |_|
#

import typing
from loguru import logger
from engine.channel import (
    Channel, Messenger
)
from mindcore import authorize
from utils import const


class Api(object):
    """
    Api

    通用异步接口适配器类，封装各类与远程服务交互的静态方法，包括数据获取、
    文件生成、命令配置加载等逻辑，适用于微服务通信、TTS 服务、自动化平台等场景。

    当前支持的服务包括语音格式元信息拉取、语音合成任务、业务用例命令获取等。
    所有接口方法均通过异步方式与后端 API 通信，支持 JSON 响应解析及异常处理。

    Notes
    -----
    - 提供统一的参数打包与请求流程，封装远程接口调用的细节。
    - 支持动态参数拼接、异常捕获、数据缓存与文件写入。
    - 可根据实际业务场景扩展其他静态方法，如上传日志、获取配置、拉取资源等。
    """

    background: list = []

    @staticmethod
    async def ask_request_get(
        url: str, key: typing.Optional[str] = None, *_, **kwargs
    ) -> dict:
        """
        通用异步 GET 请求方法。

        构造带参数的异步 GET 请求，自动附带默认参数并发送到指定 URL。支持从响应中提取指定字段，
        用于统一的业务数据获取流程，如模板信息、配置元数据等。

        Parameters
        ----------
        url : str
            请求的目标接口地址。

        key : str, optional
            可选的响应字段键名，若提供则返回对应字段的内容，否则返回整个响应字典。

        *_
            保留参数，未使用。

        **kwargs
            追加到请求参数中的动态键值对，用于拼接请求 query 参数。

        Returns
        -------
        dict
            远程服务返回的 JSON 数据（或提取后的字段值）。
        """
        headers, params = Channel.make_headers(), Channel.make_params() | kwargs
        async with Messenger() as messenger:
            resp = await messenger.poke("GET", url, headers=headers, params=params)
            return resp.json()[key] if key else resp.json()

    @staticmethod
    async def formatting() -> typing.Optional[dict]:
        """
        获取远程 TTS 服务的可用状态及支持的音频格式列表。

        Returns
        -------
        dict or None
            {
                "enabled": bool,       # 服务可用状态
                "formats": list[str],  # 支持的音频格式列表
                ...                    # 其他元信息
            }
            若服务不可用或请求异常，则返回 None。
        """
        try:
            sign_data = await Api.ask_request_get(const.SPEECH_META_URL)
            auth_info = authorize.verify_signature(sign_data)
        except Exception as e:
            return logger.debug(e)

        return auth_info.get("mode", {})

    @staticmethod
    async def remote_config() -> typing.Optional[dict]:
        """
        获取远程配置中心的全局配置数据。
        """
        try:
            sign_data = await Api.ask_request_get(const.GLOBAL_CF_URL)
            auth_info = authorize.verify_signature(sign_data)
        except Exception as e:
            return logger.debug(e)

        return auth_info.get("configuration", {})


if __name__ == '__main__':
    pass
