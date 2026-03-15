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
from mindnova import const


class Api(object):
    """Api class."""

    background: list = []

    @staticmethod
    async def ask_request_get(
        url: str, key: typing.Optional[str] = None, *_, **kwargs
    ) -> dict:
        """通用异步 GET 请求方法。"""
        headers, params = Channel.make_headers(), Channel.make_params() | kwargs
        async with Messenger() as messenger:
            resp = await messenger.poke("GET", url, headers=headers, params=params)
            return resp.json()[key] if key else resp.json()

    @staticmethod
    async def formatting() -> typing.Optional[dict]:
        """获取远程 TTS 服务的可用状态及支持的音频格式列表。"""
        try:
            sign_data = await Api.ask_request_get(const.SPEECH_META_URL)
            auth_info = authorize.verify_signature(sign_data)
        except Exception as e:
            return logger.debug(e)

        return auth_info.get("mode", {})

    @staticmethod
    async def heal_license() -> typing.Optional[dict]:
        """获取远程元素自愈服务的可用状态。"""
        try:
            sign_data = await Api.ask_request_get(const.HEAL_LIC_URL)
            auth_info = authorize.verify_signature(sign_data)
        except Exception as e:
            return logger.debug(e)

        return auth_info.get("heal_element", {})

    @staticmethod
    async def remote_config() -> typing.Optional[dict]:
        """获取远程配置中心的全局配置数据。"""
        try:
            sign_data = await Api.ask_request_get(const.GLOBAL_CF_URL)
            auth_info = authorize.verify_signature(sign_data)
        except Exception as e:
            return logger.debug(e)

        return auth_info.get("configuration", {})


if __name__ == '__main__':
    pass
