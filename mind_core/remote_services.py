# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.observability import observe_exception
from engine.channel import (
    Channel,
    Messenger
)
from mind_core.licensing import verify_signature
from mind_nova import const


class RemoteServices(object):
    """读取并验证 Mind 远程服务元数据。"""

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
            sign_data = await RemoteServices.ask_request_get(const.SPEECH_META_URL)
            auth_info = verify_signature(sign_data)
        except Exception as e:
            observe_exception("remote_service.failed", e, level="WARNING", service="speech")
            return None

        return auth_info.get("mode", {})

    @staticmethod
    async def heal_license() -> typing.Optional[dict]:
        """获取远程元素自愈服务的可用状态。"""
        try:
            sign_data = await RemoteServices.ask_request_get(const.HEAL_LIC_URL)
            auth_info = verify_signature(sign_data)
        except Exception as e:
            observe_exception("remote_service.failed", e, level="WARNING", service="heal_license")
            return None

        return auth_info.get("heal_element", {})

    @staticmethod
    async def remote_config() -> typing.Optional[dict]:
        """获取远程配置中心的全局配置数据。"""
        try:
            sign_data = await RemoteServices.ask_request_get(const.GLOBAL_CF_URL)
            auth_info = verify_signature(sign_data)
        except Exception as e:
            observe_exception("remote_service.failed", e, level="WARNING", service="global_config")
            return None

        return auth_info.get("configuration", {})


if __name__ == '__main__':
    pass
