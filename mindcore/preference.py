#  ____            __
# |  _ \ _ __ ___ / _| ___ _ __ ___ _ __   ___ ___
# | |_) | '__/ _ \ |_ / _ \ '__/ _ \ '_ \ / __/ _ \
# |  __/| | |  __/  _|  __/ | |  __/ | | | (_|  __/
# |_|   |_|  \___|_|  \___|_|  \___|_| |_|\___\___|
#

import os
import copy
import json
import httpx
import typing
import asyncio
from engine.tinker import FileAssist
from mindnova import const

DEFAULT_SCHEMA_VERSION = 2
DEFAULT_PROVIDER       = "OpenAI"


def _default_slot() -> dict[str, str]:
    return {
        "api"      : DEFAULT_PROVIDER,
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
    }


def _default_prefs() -> dict[str, typing.Any]:
    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "primary"        : _default_slot(),
        "secondary"      : _default_slot(),
    }


class Preferences(object):
    """Preferences class."""

    prefs = _default_prefs()

    def __init__(self, pref_file: typing.Any):
        self.pref_file = pref_file

    def __getstate__(self):
        return self.prefs

    def __setstate__(self, state):
        self.prefs = state

    def to_config(
        self,
        *,
        api: str = "",
        model: str = "",
        apikey: str = "",
        base_url: str = ""
    ) -> dict[str, typing.Any]:
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

        return payload

    @property
    def pref_api(self) -> str:
        return const.BASE_URL.rstrip("/") + "/api/pref"

    async def _fetch_remote_pref(self) -> dict[str, typing.Any]:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(self.pref_api)
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, dict):
            return {}
        return payload.get("data") or {}

    def _apply_primary_slot(self, payload: dict[str, typing.Any]) -> None:
        primary = payload.get("primary") or {}
        secondary = payload.get("secondary") or {}
        prefs = _default_prefs()
        prefs["schema_version"] = int(payload.get("schema_version", DEFAULT_SCHEMA_VERSION) or DEFAULT_SCHEMA_VERSION)
        prefs["primary"]["api"] = str(primary.get("api", DEFAULT_PROVIDER))
        prefs["primary"]["model"] = str(primary.get("model", ""))
        prefs["primary"]["apikey"] = str(primary.get("apikey", ""))
        prefs["primary"]["base_url"] = str(primary.get("base_url", ""))
        prefs["secondary"]["api"] = str(secondary.get("api", DEFAULT_PROVIDER))
        prefs["secondary"]["model"] = str(secondary.get("model", ""))
        prefs["secondary"]["apikey"] = str(secondary.get("apikey", ""))
        prefs["secondary"]["base_url"] = str(secondary.get("base_url", ""))
        self.prefs = prefs

    async def load_pref(self) -> None:
        payload = await self._fetch_remote_pref()
        self._apply_primary_slot(payload)
        await self.dump_pref()

    async def dump_pref(self) -> None:
        os.makedirs(os.path.dirname(self.pref_file), exist_ok=True)
        await asyncio.to_thread(
            FileAssist.dump_json, self.pref_file, self.prefs
        )

    async def load_local_pref(self) -> None:
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
