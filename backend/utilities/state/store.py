# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import json
import typing
import asyncio
import hashlib
from pathlib import Path
from backend.utilities import const


def sha10(s: str) -> str:
    """计算字符串的短 SHA1 摘要，用于快照指纹。"""
    return hashlib.sha1(s.encode(const.CHARSET, const.IGNORE)).hexdigest()[:10]


def stable_json(obj: typing.Any) -> str:
    """以稳定排序方式序列化对象，便于生成可比对指纹。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def head_tail(items: list[typing.Any], head_n: int = 5, tail_n: int = 5) -> tuple[list[typing.Any], list[typing.Any]]:
    """截取列表头尾两段摘要数据。"""
    head = items[:head_n]
    tail = items[-tail_n:] if len(items) > head_n else []
    return head, tail


def parent_stats(paths: typing.Iterable[str], limit: int = 6) -> list[dict[str, typing.Any]]:
    """统计路径集合中各父目录的聚合分布。"""
    parents: dict[str, int] = {}
    for p in paths:
        parent = Path(str(p)).parent.name
        parents[parent] = parents.get(parent, 0) + 1
    return [
        {"parent": k, "count": v}
        for k, v in sorted(parents.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    ]


def slim_path(p: str) -> dict[str, str]:
    """把完整路径压缩成名称、父目录和扩展名摘要。"""
    pp: Path = Path(str(p))
    return {
        "name"   : pp.name,
        "parent" : pp.parent.name,
        "ext"    : pp.suffix.lower()
    }


def slim_dir(p: str) -> dict[str, str]:
    """把目录路径压缩成名称和父目录摘要。"""
    pp: Path = Path(str(p))
    return {"name": pp.name, "parent": pp.parent.name}


def kw_summary(v: typing.Any, max_items: int = 8) -> dict[str, typing.Any]:
    """把任意对象压缩成适合展示和比对的摘要结构。"""
    if v is None:
        return {"type": "none", "scene": []}

    if isinstance(v, str):
        return {"type": "str", "scene": [v]}

    if isinstance(v, (list, tuple, set)):
        items = sorted(str(x) for x in v)
        return {"type": type(v).__name__, "scene": items[:max_items], "n": len(items)}

    if isinstance(v, dict):
        keys = sorted(str(k) for k in v.keys())
        return {"type": "dict", "scene": keys[:max_items], "n": len(keys)}

    s = repr(v)
    if len(s) > 160:
        s = s[:160] + "…"
    return {"type": type(v).__name__, "repr": s, "scene": []}


class PathSessionStore(object):

    def __init__(self, name: str):
        """初始化一个基于路径值的会话仓库。"""
        self.name = name
        self.items: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> typing.Optional[str]:
        """读取指定会话键对应的路径值。"""
        async with self.lock:
            return self.items.get(key)

    async def set(self, key: str, path: str) -> None:
        """写入指定会话键对应的路径值。"""
        async with self.lock:
            self.items[str(key)] = str(path)

    async def pop(self, key: str) -> typing.Optional[str]:
        """删除并返回指定会话键对应的路径值。"""
        async with self.lock:
            return self.items.pop(key, None)

    async def snapshot(self, session_id: typing.Optional[str] = None, head_n: int = 5, tail_n: int = 5) -> dict[str, typing.Any]:
        """生成路径会话仓库的详情或摘要快照。"""
        async with self.lock:
            items = dict(self.items)

        if session_id:
            path = items.get(session_id)

            exists = bool(path) and os.path.exists(path)
            fp_src = f"{session_id}|{path or ''}|{int(exists)}"

            return {
                self.name: {
                    "id"     : session_id,
                    "path"   : path,
                    "exists" : exists,
                    "fp"     : sha10(fp_src)
                }
            }

        keys  = sorted(items.keys())
        count = len(keys)

        head, tail = head_tail(keys, head_n=head_n, tail_n=tail_n)

        focus_pairs = [(k, items.get(k, "")) for k in (head + tail)]
        fp_src      = "|".join([f"{k}={v}" for k, v in focus_pairs])

        return {
            self.name: {
                "count"        : count,
                "head"         : [{"id": k, **slim_dir(items[k])} for k in head],
                "tail"         : [{"id": k, **slim_dir(items[k])} for k in tail],
                "parent_stats" : parent_stats(items.values()),
                "fp"           : "0000000000" if count == 0 else sha10(fp_src)
            }
        }


class ItemSessionStore(object):

    def __init__(self, name: str):
        """初始化一个可存放任意对象的会话仓库。"""
        self.name = name
        self.items: dict[str, typing.Any] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> typing.Any:
        """读取指定会话键对应的对象值。"""
        async with self.lock:
            return self.items.get(key)

    async def set(self, key: str, value: typing.Any) -> None:
        """写入指定会话键对应的对象值。"""
        async with self.lock:
            self.items[str(key)] = value

    async def pop(self, key: str) -> typing.Any:
        """删除并返回指定会话键对应的对象值。"""
        async with self.lock:
            return self.items.pop(key, None)

    async def snapshot(self, session_id: typing.Optional[str] = None, head_n: int = 5, tail_n: int = 5) -> dict[str, typing.Any]:
        """生成对象会话仓库的详情或摘要快照。"""
        async with self.lock:
            items = dict(self.items)

        if session_id:
            item   = kw_summary(items.get(session_id))
            fp_src = stable_json({"id": session_id, "item": item})

            return {
                self.name: {
                    "id"   : session_id,
                    "item" : item,
                    "fp"   : "0000000000" if items.get(session_id) is None else sha10(fp_src)
                }
            }

        keys  = sorted(items.keys())
        count = len(keys)

        head, tail = head_tail(keys, head_n=head_n, tail_n=tail_n)

        focus_items = [{"id": k, "item": kw_summary(items.get(k))} for k in (head + tail)]
        fp_src      = stable_json({"focus": focus_items})

        return {
            self.name: {
                "count" : count,
                "head"  : [{"id": k, "item": kw_summary(items.get(k))} for k in head],
                "tail"  : [{"id": k, "item": kw_summary(items.get(k))} for k in tail],
                "fp"    : "0000000000" if count == 0 else sha10(fp_src)
            }
        }


class VideoQueue(object):

    def __init__(self):
        """初始化用于跨工具传递视频文件的内存队列。"""
        self.items: list[str] = []
        self.lock = asyncio.Lock()

    async def append(self, path: str) -> None:
        """向视频队列末尾追加一个文件路径。"""
        async with self.lock:
            self.items.append(path)

    async def take_all(self) -> list[str]:
        """原子取出队列中的全部文件并清空当前队列。"""
        async with self.lock:
            items = list(self.items)
            self.items.clear()
        return items

    async def items_copy(self) -> list[str]:
        """返回视频队列的当前副本。"""
        async with self.lock:
            return list(self.items)

    async def snapshot(self, head_n: int = 5, tail_n: int = 5) -> dict[str, typing.Any]:
        """生成视频队列的摘要快照。"""
        items = await self.items_copy()
        count = len(items)

        head_keys, tail_keys = head_tail(items, head_n=head_n, tail_n=tail_n)

        head = [slim_path(x) for x in head_keys]
        tail = [slim_path(x) for x in tail_keys]

        fp_src = "|".join([f"{d['parent']}/{d['name']}" for d in (head + tail)])

        return {
            "video_list": {
                "count"        : count,
                "head"         : head,
                "tail"         : tail,
                "parent_stats" : parent_stats(items),
                "fp"           : "0000000000" if count == 0 else sha10(fp_src)
            }
        }


if __name__ == '__main__':
    pass
