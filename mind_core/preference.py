# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import copy
import json
import httpx
import typing
import asyncio
from engine.tinker import FileAssist
from mind_nova import const

DEFAULT_SCHEMA_VERSION = 2
DEFAULT_PROVIDER       = "OpenAI"
DEFAULT_ROUTE          = "responses"


def _default_slot() -> dict[str, str]:
    """返回单个模型槽位的默认配置。"""
    return {
        "api"      : DEFAULT_PROVIDER,
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
        "route"    : DEFAULT_ROUTE
    }


def _default_prefs() -> dict[str, typing.Any]:
    """返回偏好配置的默认结构。"""
    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "primary"        : _default_slot()
    }


class Preferences(object):
    """管理偏好配置的读取、规范化与落盘。"""

    def __init__(self, pref_file: typing.Any):
        """初始化偏好文件路径和默认配置。"""
        self.pref_file = pref_file
        self.prefs     = _default_prefs()

    def __getstate__(self):
        """提供序列化时的状态导出。"""
        return self.prefs

    def __setstate__(self, state):
        """在反序列化时恢复内部状态。"""
        self.prefs = state

    def to_config(
        self,
        *,
        api: str = "",
        model: str = "",
        apikey: str = "",
        base_url: str = "",
        route: str = ""
    ) -> dict[str, typing.Any]:
        """基于当前配置生成运行时可用的配置副本。"""
        payload = copy.deepcopy(self.prefs)
        primary = payload.setdefault("primary", {})

        if api:
            primary["api"] = api
        if model:
            primary["model"] = model
        if apikey:
            primary["apikey"] = apikey
        if base_url:
            primary["base_url"] = base_url
        if route:
            primary["route"] = route

        return payload

    @property
    def pref_api(self) -> str:
        """返回远端偏好接口地址。"""
        return const.BASE_URL.rstrip("/") + "/api/pref"

    async def _fetch_remote_pref(self) -> dict[str, typing.Any]:
        """从本地服务拉取最新偏好配置。"""
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            resp = await client.get(self.pref_api)
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, dict):
            return {}
        return payload.get("data") or {}

    @staticmethod
    def _slot_configured(slot: typing.Any) -> bool:
        """判断 secondary 是否满足有效写入条件。"""
        if not isinstance(slot, dict):
            return False

        return bool(
            str(slot.get("model", "")).strip()
            and str(slot.get("apikey", "")).strip()
        )

    def _apply_primary_slot(self, payload: dict[str, typing.Any]) -> None:
        """将输入配置规范化为内部使用的偏好结构。"""
        primary   = payload.get("primary") or {}
        secondary = payload.get("secondary")

        prefs = {
            "schema_version": int(payload.get("schema_version", DEFAULT_SCHEMA_VERSION) or DEFAULT_SCHEMA_VERSION),
            "primary": {
                "api"      : str(primary.get("api", DEFAULT_PROVIDER)),
                "model"    : str(primary.get("model", "")),
                "apikey"   : str(primary.get("apikey", "")),
                "base_url" : str(primary.get("base_url", "")),
                "route"    : str(primary.get("route", DEFAULT_ROUTE) or DEFAULT_ROUTE)
            }
        }

        if self._slot_configured(secondary):
            prefs["secondary"] = {
                "api"      : str(secondary.get("api", DEFAULT_PROVIDER)),
                "model"    : str(secondary.get("model", "")),
                "apikey"   : str(secondary.get("apikey", "")),
                "base_url" : str(secondary.get("base_url", "")),
                "route"    : str(secondary.get("route", DEFAULT_ROUTE) or DEFAULT_ROUTE)
            }

        self.prefs = prefs

    async def load_pref(self) -> None:
        """从远端加载偏好并同步写入本地文件。"""
        payload = await self._fetch_remote_pref()
        self._apply_primary_slot(payload)
        await self.dump_pref()

    async def dump_pref(self) -> None:
        """将当前偏好配置写入本地 json 文件。"""
        os.makedirs(os.path.dirname(self.pref_file), exist_ok=True)
        await asyncio.to_thread(
            FileAssist.dump_json, self.pref_file, self.prefs
        )

    async def load_local_pref(self) -> None:
        """从本地 json 读取偏好，缺失时自动创建默认文件。"""
        try:
            payload = await asyncio.to_thread(
                FileAssist.read_json, self.pref_file
            )
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            await self.dump_pref()
            return None

        self._apply_primary_slot(payload if isinstance(payload, dict) else {})


if __name__ == '__main__':
    pass
