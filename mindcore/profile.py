#   ____             __ _ _
#  |  _ \ _ __ ___  / _(_) | ___
#  | |_) | '__/ _ \| |_| | |/ _ \
#  |  __/| | | (_) |  _| | |  __/
#  |_|   |_|  \___/|_| |_|_|\___|
#

import os
import json
import typing
import asyncio
from pathlib import Path
from engine.tinker import FileAssist
from mindcore.design import Design


class Preferences(object):
    """Preferences class."""

    prefs = {
        "api"      : "OpenAI",
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
    }

    def __init__(self, pref_file: typing.Any):
        self.pref_file = pref_file

    def __getstate__(self):
        return self.prefs

    def __setstate__(self, state):
        self.prefs = state

    @property
    def api(self):
        return self.prefs["api"]

    @property
    def model(self):
        return self.prefs["model"]

    @property
    def apikey(self):
        return self.prefs["apikey"]

    @property
    def base_url(self) -> str:
        return self.prefs.get("base_url", "")

    @api.setter
    def api(self, value: typing.Any):
        self.prefs["api"] = value

    @model.setter
    def model(self, value: typing.Any):
        self.prefs["model"] = value

    @apikey.setter
    def apikey(self, value: typing.Any):
        self.prefs["apikey"] = value

    @base_url.setter
    def base_url(self, value: typing.Any) -> None:
        self.prefs["base_url"] = value

    def to_config(
        self,
        *,
        api: str = "",
        model: str = "",
        apikey: str = "",
        base_url: str = ""
    ) -> dict[str, typing.Any]:

        return {
            "api"      : api or self.api,
            "model"    : model or self.model,
            "apikey"   : apikey or self.apikey,
            "base_url" : base_url or self.base_url
        }

    async def load_pref(self) -> None:
        try:
            user_align = await asyncio.to_thread(
                FileAssist.read_json, self.pref_file
            )

            self.api      = user_align.get("api", "")
            self.model    = user_align.get("model", "")
            self.apikey   = user_align.get("apikey", "")
            self.base_url = user_align.get("base_url", "")

        except (FileNotFoundError, json.decoder.JSONDecodeError):
            await self.dump_pref()

    async def dump_pref(self) -> None:
        os.makedirs(os.path.dirname(self.pref_file), exist_ok=True)
        await asyncio.to_thread(
            FileAssist.dump_json, self.pref_file, self.prefs
        )

    async def view_pref(self) -> None:
        if not Path(self.pref_file).exists():
            await self.dump_pref()

        Design.console.print()
        Design.build_file_tree(self.pref_file)
        await FileAssist.open(self.pref_file)
        await self.load_pref()

        return Design.console.print()


if __name__ == '__main__':
    pass
