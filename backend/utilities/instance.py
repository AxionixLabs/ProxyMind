#  ___           _
# |_ _|_ __  ___| |_ __ _ _ __   ___ ___
#  | || '_ \/ __| __/ _` | '_ \ / __/ _ \
#  | || | | \__ \ || (_| | | | | (_|  __/
# |___|_| |_|___/\__\__,_|_| |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import asyncio
import hashlib
from pathlib import Path
from backend.mcp_core.core_framix import Framix
from backend.mcp_core.core_memrix import Memrix
from backend.mcp_hub.hub_medias import (
    FFmpeg, Player
)
from backend.mcp_hub.hub_record import Record
from backend.utilities import const


class Ins(object):

    station: str = sys.platform

    sessions: dict[str, Record] = {}
    sessions_lock: asyncio.Lock = asyncio.Lock()

    video_list: list[str] = []
    video_lock: asyncio.Lock = asyncio.Lock()

    framix: Framix = Framix()
    memrix: Memrix = Memrix()
    ffmpeg: FFmpeg = FFmpeg()
    player: Player = Player()

    @staticmethod
    def slim_path(p: str) -> dict:
        pp: Path = Path(str(p))
        return {
            "name"   : pp.name,
            "parent" : pp.parent.name,
            "ext"    : pp.suffix.lower()
        }

    @staticmethod
    def video_list_snapshot() -> dict:
        head_n, tail_n = 5, 5
        n = len(Ins.video_list)
        head = [Ins.slim_path(x) for x in Ins.video_list[:head_n]]
        tail = [Ins.slim_path(x) for x in (Ins.video_list[-tail_n:] if n > head_n else [])]

        fp_src = "|".join([f"{d['parent']}/{d['name']}" for d in (head + tail)])
        fp = hashlib.sha1(fp_src.encode(const.CHARSET, const.IGNORE)).hexdigest()[:10]

        parents = {}
        for p in Ins.video_list:
            parent = Path(str(p)).parent.name
            parents[parent] = parents.get(parent, 0) + 1
        parent_stats = [
            {"parent": k, "count": v}
            for k, v in sorted(parents.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ]

        return {
            "instance": {
                "video_list": {
                    "count"        : n,
                    "head"         : head,
                    "tail"         : tail,
                    "parent_stats" : parent_stats,
                    "fp"           : fp
                }
            }
        }


if __name__ == '__main__':
    pass
