#   ____             __ _ _
#  |  _ \ _ __ ___  / _(_) | ___
#  | |_) | '__/ _ \| |_| | |/ _ \
#  |  __/| | | (_) |  _| | |  __/
#  |_|   |_|  \___/|_| |_|_|\___|
#
# Notes: ✦ Mind ✦ Copyright (c) 2026.
# Notes: Licensed use only · Redistribution requires explicit permission and approval.

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
        "model"  : "",
        "apikey" : ""
    }

    def __init__(self, pref_file: typing.Any):
        self.pref_file = pref_file

    def __getstate__(self):
        return self.prefs

    def __setstate__(self, state):
        self.prefs = state

    @property
    def model(self):
        return self.prefs["model"]

    @property
    def apikey(self):
        return self.prefs["apikey"]

    @model.setter
    def model(self, value: typing.Any):
        self.prefs["model"] = value

    @apikey.setter
    def apikey(self, value: typing.Any):
        self.prefs["apikey"] = value

    async def load_pref(self) -> None:
        try:
            user_align = await asyncio.to_thread(
                FileAssist.read_json, self.pref_file
            )

            self.model  = user_align.get("model", "")
            self.apikey = user_align.get("apikey", "")

        except (FileNotFoundError, json.decoder.JSONDecodeError):
            await self.dump_pref()

    async def dump_pref(self) -> None:
        os.makedirs(os.path.dirname(self.pref_file), exist_ok=True)
        await asyncio.to_thread(
            FileAssist.dump_json, self.pref_file, self.prefs
        )

    async def view_perf(self) -> None:
        if not Path(self.pref_file).exists():
            await self.dump_pref()

        Design.console.print()
        Design.build_file_tree(self.pref_file)
        await FileAssist.open(self.pref_file)
        await self.load_pref()

        return Design.console.print()


if __name__ == '__main__':
    pass
