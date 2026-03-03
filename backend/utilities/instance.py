#  ___           _
# |_ _|_ __  ___| |_ __ _ _ __   ___ ___
#  | || '_ \/ __| __/ _` | '_ \ / __/ _ \
#  | || | | \__ \ || (_| | | | | (_|  __/
# |___|_| |_|___/\__\__,_|_| |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import json
import typing
import asyncio
import hashlib
from pathlib import Path
from backend.mcp_core.core_framix import Framix
from backend.mcp_core.core_memrix import Memrix
from backend.mcp_core.core_nexus import Nexus
from backend.mcp_hub.hub_medias import (
    FFmpeg, Player
)
from backend.mcp_hub.hub_record import Record
from backend.utilities import const


class Ins(object):

    station: str = sys.platform

    sessions: dict[str, Record] = {}
    sessions_lock: asyncio.Lock = asyncio.Lock()

    fx_report_session: dict[str, typing.Any] = {}
    mx_report_session: dict[str, typing.Any] = {}

    video_list: list[str] = []
    video_lock: asyncio.Lock = asyncio.Lock()

    framix: Framix = Framix(
        fx_report_session=fx_report_session
    )
    memrix: Memrix = Memrix(
        mx_report_session=mx_report_session
    )

    nexus: Nexus = Nexus()

    ffmpeg: FFmpeg = FFmpeg()
    player: Player = Player()

    @staticmethod
    def instance_snapshots() -> dict[str, dict[str, typing.Any]]:
        """返回快照集合（每个都是单独快照）"""
        return {
            "instance": {
                **Ins.video_list_snapshot(),
                **Ins.fx_report_snapshot(),
                **Ins.mx_report_snapshot()
            }
        }

    @staticmethod
    def sha10(s: str) -> str:
        return hashlib.sha1(s.encode(const.CHARSET, const.IGNORE)).hexdigest()[:10]

    @staticmethod
    def stable_json(obj: typing.Any) -> str:
        """用于做 fp 的稳定序列化：只做摘要时用，避免直接 dump 巨大对象。"""
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def slim_path(p: str) -> dict:
        pp: Path = Path(str(p))
        return {
            "name"   : pp.name,
            "parent" : pp.parent.name,
            "ext"    : pp.suffix.lower()
        }

    @staticmethod
    def slim_dir(p: str) -> dict:
        pp: Path = Path(str(p))
        return {"name": pp.name, "parent": pp.parent.name}

    @staticmethod
    def kw_summary(v: typing.Any, max_items: int = 8) -> dict:
        """把 value 摘要成可展示、可 hash 的结构。"""
        if v is None:
            return {"type": "none", "scene": []}

        if isinstance(v, str):
            return {"type": "str", "scene": [v]}

        if isinstance(v, (list, tuple, set)):
            items = [str(x) for x in v]
            items = sorted(items)
            return {"type": type(v).__name__, "scene": items[:max_items], "n": len(items)}

        if isinstance(v, dict):
            keys = [str(k) for k in v.keys()]
            keys = sorted(keys)
            return {"type": "dict", "scene": keys[:max_items], "n": len(keys)}

        s = repr(v)
        if len(s) > 160:
            s = s[:160] + "…"
        return {"type": type(v).__name__, "repr": s, "scene": []}

    @staticmethod
    def fx_report_snapshot(session_id: typing.Optional[str] = None, head_n: int = 5, tail_n: int = 5) -> dict:
        if session_id:
            path   = Ins.fx_report_session.get(session_id)
            exists = bool(path) and os.path.exists(path)
            fp_src = f"{session_id}|{path or ''}|{int(exists)}"

            return {
                "fx_report_session": {
                    "id"     : session_id,
                    "path"   : path,
                    "exists" : exists,
                    "fp"     : Ins.sha10(fp_src)
                }
            }

        keys = sorted(Ins.fx_report_session.keys())
        n    = len(keys)
        head = keys[:head_n]
        tail = keys[-tail_n:] if n > head_n else []

        focus       = head + tail
        focus_pairs = [(k, Ins.fx_report_session.get(k, "")) for k in focus]
        fp_src      = "|".join([f"{k}={v}" for k, v in focus_pairs])
        fp          = "0000000000" if n == 0 else Ins.sha10(fp_src)

        parents: dict[str, int] = {}
        for p in Ins.fx_report_session.values():
            parent = Path(str(p)).parent.name
            parents[parent] = parents.get(parent, 0) + 1
        parent_stats = [
            {"parent": k, "count": v}
            for k, v in sorted(parents.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ]

        head_dirs = [{"id": k, **Ins.slim_dir(Ins.fx_report_session[k])} for k in head]
        tail_dirs = [{"id": k, **Ins.slim_dir(Ins.fx_report_session[k])} for k in tail]

        return {
            "fx_report_session": {
                "count"        : n,
                "head"         : head_dirs,
                "tail"         : tail_dirs,
                "parent_stats" : parent_stats,
                "fp"           : fp
            }
        }

    @staticmethod
    def mx_report_snapshot(session_id: typing.Optional[str] = None, head_n: int = 5, tail_n: int = 5) -> dict:
        if session_id:
            v      = Ins.mx_report_session.get(session_id)
            item   = Ins.kw_summary(v)
            fp_src = Ins.stable_json({"id": session_id, "item": item})
            fp     = "0000000000" if v is None else Ins.sha10(fp_src)
            return {
                "mx_report_session": {
                    "id"   : session_id,
                    "item" : item,
                    "fp"   : fp
                }
            }

        keys = sorted(Ins.mx_report_session.keys())
        n    = len(keys)
        head = keys[:head_n]
        tail = keys[-tail_n:] if n > head_n else []

        focus = head + tail
        focus_items = [
            {"id": k, "item": Ins.kw_summary(Ins.mx_report_session.get(k))}
            for k in focus
        ]
        fp_src = Ins.stable_json({"focus": focus_items})
        fp = "0000000000" if n == 0 else Ins.sha10(fp_src)

        head_items = [{"id": k, "item": Ins.kw_summary(Ins.mx_report_session.get(k))} for k in head]
        tail_items = [{"id": k, "item": Ins.kw_summary(Ins.mx_report_session.get(k))} for k in tail]

        return {
            "mx_report_session": {
                "count" : n,
                "head"  : head_items,
                "tail"  : tail_items,
                "fp"    : fp
            }
        }

    @staticmethod
    def video_list_snapshot() -> dict:
        head_n, tail_n = 5, 5
        n = len(Ins.video_list)
        head = [Ins.slim_path(x) for x in Ins.video_list[:head_n]]
        tail = [Ins.slim_path(x) for x in (Ins.video_list[-tail_n:] if n > head_n else [])]

        fp_src = "|".join([f"{d['parent']}/{d['name']}" for d in (head + tail)])
        fp = "0000000000" if n == 0 else Ins.sha10(fp_src)

        parents: dict[str, int] = {}
        for p in Ins.video_list:
            parent = Path(str(p)).parent.name
            parents[parent] = parents.get(parent, 0) + 1
        parent_stats = [
            {"parent": k, "count": v}
            for k, v in sorted(parents.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ]

        return {
            "video_list": {
                "count"        : n,
                "head"         : head,
                "tail"         : tail,
                "parent_stats" : parent_stats,
                "fp"           : fp
            }
        }


if __name__ == '__main__':
    pass
