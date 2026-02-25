#  ____            _ _
# / ___|  ___ __ _| (_)_ __   __ _
# \___ \ / __/ _` | | | '_ \ / _` |
#  ___) | (_| (_| | | | | | | (_| |
# |____/ \___\__,_|_|_|_| |_|\__, |
#                            |___/
#

import time
import typing
import asyncio
from dataclasses import dataclass
from loguru import logger
from mindnova import request


@dataclass
class PackItem:
    name: str
    message: str
    meta: dict[str, str]


@dataclass
class PackItemResult:
    name: str
    ok: bool
    cost_s: float
    error: typing.Optional[str] = None


@dataclass
class PackReport:
    total: int
    executed: int
    ok: int
    failed: int
    skipped: int
    results: list[PackItemResult]


class EventReport(object):
    """
    稳定事件上报器（Strong Ordering）

    目标：
    1) 事件发送顺序稳定：emit() 进队列，后台单 worker 串行发送
    2) 事件可重排：自动添加 seq（单会话内递增）
    3) 不阻塞主流程：emit() 只做 put_nowait；队列满则丢并打 debug
    4) 支持 flush()/close()：确保 pack_done 等“最后事件”一定在末尾到达
    """

    def __init__(
        self,
        cid: str,
        sid: str,
        *,
        queue_size: int = 2000,
        timeout: float = 30.0
    ):
        self.cid = cid
        self.sid = sid
        self.timeout = timeout

        self.seq = 0
        self.q: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue(maxsize=queue_size)

        self.stop = asyncio.Event()
        self.worker: typing.Optional[asyncio.Task] = None

    async def open(self) -> None:
        """启动后台发送 worker（建议在 pack_start 前调用）"""
        if self.worker and not self.worker.done():
            return None
        self.worker = asyncio.create_task(self.run())

    def emit(self, event: dict[str, typing.Any]) -> None:
        """
        非阻塞投递事件。
        - 自动注入 cid/sid/ts/seq
        - 队列满则丢弃（避免拖死主链路）
        """
        try:
            self.seq += 1
            ev = dict(event or {})
            ev.setdefault("ts", time.time())
            ev["cid"] = self.cid
            ev["sid"] = self.sid
            ev["seq"] = self.seq

            self.q.put_nowait(ev)
        except asyncio.QueueFull:
            logger.debug(f"[events] drop(queue_full) type={event.get('type')}")
        except RuntimeError:
            logger.debug(f"[events] drop(no_loop) type={event.get('type')}")

    async def run(self) -> None:
        """单 worker：严格按队列顺序发送"""
        while True:
            if self.stop.is_set() and self.q.empty():
                return None

            try:
                ev = await asyncio.wait_for(self.q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                await request.post_stream_event(self.cid, self.sid, ev, timeout=self.timeout)
            except Exception as e:
                # 上报失败：不影响主流程
                logger.debug(f"[events] post fail: {e!r} type={ev.get('type')} seq={ev.get('seq')}")
            finally:
                self.q.task_done()

    async def flush(self) -> None:
        """等待队列清空（所有已 emit 的事件都发完）"""
        await self.q.join()

    async def close(self) -> None:
        """优雅停止：先 flush，再退出 worker"""
        await self.flush()
        self.stop.set()
        if self.worker:
            await self.worker


def pack_parse(text: str) -> list[PackItem]:
    """
    将 .md/.txt 文本解析为 PackItem 列表（自然语言用例序列）。

    约定格式：
    - 用例分隔符：某一行 strip() 后等于 '---'，表示一个用例块结束
    - 每个用例块允许在开头写若干行元信息（只解析连续的开头 # 行）：
        # key: value
      目前约定支持：name（可选）
    - 元信息结束后，剩余内容原样拼接为自然语言 message（交给 stream_plan 生成步骤序列）

    返回：
      list[PackItem(name, message, meta)]
    """

    # 1) 按分隔符切分为多个块（每个块对应一个用例）
    blocks: list[list[str]] = []
    cur: list[str] = []

    for line in text.splitlines():
        if line.strip() == "---":
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)

    if cur:
        blocks.append(cur)

    items: list[PackItem] = []
    auto_idx = 0

    # 2) 逐块解析：块头元信息 + 自然语言正文
    for b in blocks:
        # 去掉块前后的空行，避免产生空用例
        while b and not b[0].strip():
            b.pop(0)
        while b and not b[-1].strip():
            b.pop()
        if not b: continue

        meta: dict[str, str] = {}
        i = 0

        # 只解析块开头连续的 # 行作为元信息；一旦遇到非 # 行即停止解析
        while i < len(b) and b[i].lstrip().startswith("#"):
            raw = b[i].lstrip()[1:].strip()  # 去掉开头 '#'
            if ":" in raw:
                k, v = raw.split(":", 1)
                meta[k.strip().lower()] = v.strip()
            i += 1

        # 将剩余内容作为自然语言 message（会交给 plan 模式/模型编译为 steps）
        msg = "\n".join(b[i:]).strip()
        if not msg: continue

        # name 可选，不写则生成 item_001 / item_002 ...
        auto_idx += 1
        name = meta.get("name") or f"item_{auto_idx:03d}"

        items.append(PackItem(name=name, message=msg, meta=meta))

    return items


if __name__ == '__main__':
    pass
