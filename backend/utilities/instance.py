#  ___           _
# |_ _|_ __  ___| |_ __ _ _ __   ___ ___
#  | || '_ \/ __| __/ _` | '_ \ / __/ _ \
#  | || | | \__ \ || (_| | | | | (_|  __/
# |___|_| |_|___/\__\__,_|_| |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import asyncio
from contextvars import ContextVar
from backend.mcp_core.core_framix import Framix
from backend.mcp_core.core_memrix import Memrix
from backend.mcp_hub.hub_medias import (
    FFmpeg, Player
)
from backend.mcp_hub.hub_record import Record


class Ins(object):

    CTX: ContextVar[dict] = ContextVar("CTX", default={})

    station: str = sys.platform

    sessions: dict[str, Record] = {}
    sessions_lock: asyncio.Lock = asyncio.Lock()

    video_list: list[str] = []
    video_lock: asyncio.Lock = asyncio.Lock()

    framix: Framix = Framix()
    memrix: Memrix = Memrix()
    ffmpeg: FFmpeg = FFmpeg()
    player: Player = Player()


if __name__ == '__main__':
    pass
