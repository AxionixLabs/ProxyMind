# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
from dataclasses import (
    dataclass, field
)

# Notes: ==== Memrix ====
ENGINE_RE = re.compile(r"(?<=--forge\s).*")
REPORT_RE = re.compile(r"(\b[A-Za-z]:\\[^\r\n]*?\.html\b|\b/[^ \r\n]*?\.html\b)")

# Notes: ==== Framix ====
FPS_RE      = re.compile(r"fps=(\d+)")
SHAPE_RE    = re.compile(r"(?<=视频尺寸:\s).*")
REAL_FPS_RE = re.compile(r"(?<=实际帧率:\s).*")
COST_RE     = re.compile(r"(?<=视频时长:\s).*")
FILTER_RE   = re.compile(r"(?<=视频过滤:\s).*")
EXTRA_RE    = re.compile(r"Arkiv", re.I)


def _false(_: str) -> bool:
    return False


def _extract(_: dict, __: str) -> None:
    return None


@dataclass
class GateSpec:
    name: str
    belongs: typing.Callable[[str], bool]
    start_when: typing.Callable[[str], bool]
    start_extract: typing.Callable[[dict, str], None]
    start_ready: typing.Callable[[dict], bool]
    close_when: typing.Callable[[str], bool] = _false
    close_extract: typing.Callable[[dict, str], None] = _extract
    close_ready: typing.Callable[[str], bool] = _false


@dataclass
class GateState:
    mode: typing.Literal[
        "seek_start", "start", "running", "close", "done"
    ] = "seek_start"
    start: dict = field(default_factory=dict)
    close: dict = field(default_factory=dict)
    start_emitted: bool = False
    close_emitted: bool = False


class LineBuffer(object):
    """完整行缓冲（适用于 stdout/stderr 的随机分块）。"""

    def __init__(self, *, treat_cr_as_newline: bool = False, keep_cr: bool = False):
        self.buf: str = ""
        self.treat_cr_as_newline = treat_cr_as_newline
        self.keep_cr = keep_cr

    def reset(self) -> None:
        self.buf = ""

    def feed(self, chunk: str) -> list[str]:
        """输入任意 chunk（可能包含多行、也可能半行）,输出所有完整行（不含行尾换行符）。"""
        if not chunk: return []

        self.buf += chunk
        out: list[str] = []

        i = 0
        n = len(self.buf)

        while i < n:
            ch = self.buf[i]

            if ch == "\n":
                line = self.buf[:i]

                if line.endswith("\r"):
                    line = line[:-1]
                out.append(line)
                self.buf = self.buf[i + 1 :]
                i = 0
                n = len(self.buf)
                continue

            if ch == "\r":
                if i + 1 < n and self.buf[i + 1] == "\n":
                    out.append(self.buf[:i])
                    self.buf = self.buf[i + 2 :]
                    i = 0
                    n = len(self.buf)
                    continue

                if i + 1 == n:
                    break

                if self.treat_cr_as_newline:
                    out.append(self.buf[:i])
                    self.buf = self.buf[i + 1 :]
                    i = 0
                    n = len(self.buf)
                    continue
                else:
                    if not self.keep_cr:
                        self.buf = self.buf[:i] + self.buf[i + 1 :]
                        n -= 1
                        continue
                    i += 1
                    continue

            i += 1

        return out

    def flush(self) -> list[str]:
        """流结束时，把最后半行吐出来（如果有）。"""
        if not self.buf:
            return []

        last = self.buf
        # 去掉末尾孤立 '\r'（常见于拆包或覆盖输出）
        if last.endswith("\r"):
            last = last[:-1]

        self.buf = ""
        last = last.strip()
        return [last] if last else []


class Pick(object):

    @staticmethod
    def pick_arrow(line: str) -> str:
        if "->" in line:
            return line.split("->", 1)[1].strip()
        return line

    @staticmethod
    def pick_value(line: str) -> str:
        if " | " in line:
            return line.split(" | ", 1)[1].strip()
        return line


class GateMachine(object):

    def __init__(self, spec: GateSpec):
        self.spec = spec
        self.state = GateState()

    def feed_chunk(self, chunk: str) -> typing.Optional[dict]:
        if (state := self.state).mode == "done":
            return None

        if not self.spec.belongs(chunk):
            return None

        payload = Pick.pick_value(chunk)

        # [Stage 1] seek_start：没看到 start 信号前，完全不采集
        if state.mode == "seek_start":
            if self.spec.start_when(payload): state.mode = "start"
            else: return None

        # [Stage 2] start：采集 start 字段，满足 start_ready 时只发一次，然后进入 running
        if state.mode == "start":
            self.spec.start_extract(state.start, payload)

            if not state.start_emitted and self.spec.start_ready(state.start):
                state.start_emitted = True
                state.mode = "running"
                return {"phase": "start", "tool": self.spec.name, **state.start}

            # start 阶段也允许直接命中 close_when（避免漏掉 close 起点）
            if self.spec.close_when(payload):
                state.mode = "close"
            return None

        # [Stage 3] running：不采集，只检测是否进入 close
        if state.mode == "running":
            if self.spec.close_when(payload):
                state.mode = "close"
            return None

        # [Stage 4] close：采集 close；ready 发一次；然后 done
        if state.mode == "close":
            self.spec.close_extract(state.close, payload)

            if not state.close_emitted and self.spec.close_ready(payload):
                state.close_emitted = True
                state.mode = "done"
                state.close.pop("_p", None)
                state.close.pop("_comb_on", None)
                state.close.pop("_comb", None)
                return {"phase": "close", "tool": self.spec.name, **state.close}

            return None

        return None


def mx_belongs(chunk: str) -> bool:
    return bool(chunk and chunk.strip())


def mx_start_when(payload: str) -> bool:
    return "Engine Start" in payload or "Report Start" in payload


def mx_start_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if payload.startswith("时间"):
        bucket["time"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("应用"):
        bucket["app"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("频率"):
        bucket["freq"] = Pick.pick_arrow(payload) or payload
    elif "Token:" in payload:
        if m := re.search(r"(?<=Token:\s).*", payload):
            bucket["token"] = m.group()

    if "Handler Done" in payload:
        if m := re.search(r"\b([A-Za-z]+_\d+)\b(?=\s+Handler\s+Done\b)", payload):
            bucket.setdefault("handlers", []).append(m.group(1))


def mx_start_ready(data: dict) -> bool:
    return all(k in data for k in ("time", "app", "freq", "token")) or "handlers" in data


def mx_close_when(payload: str) -> bool:
    return (
        "Finishing up" in payload
        or "Awaiting background sync" in payload
        or "Cancelled:" in payload
        or "Wait background tasks" in payload
        or "Polymerization" in payload
    )


def mx_close_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if "--forge" in payload:
        if m := ENGINE_RE.search(payload):
            bucket["suffix"] = m.group()

    pending, m = bucket.get("_p", ""), None

    # 先试本行/拼接匹配
    if m := REPORT_RE.search(payload) or REPORT_RE.search(pending + payload):
        bucket["html"] = m.group(1)
        bucket.pop("_p", None)
        return

    if "Library\\Tree" in payload or "Library/Tree" in payload:
        bucket["_p"] = payload


def mx_close_ready(payload: str) -> bool:
    return "Engine Close" in payload or "Report Close" in payload


MX_SPEC = GateSpec(
    name="memrix",
    belongs=mx_belongs,
    start_when=mx_start_when,
    start_extract=mx_start_extract,
    start_ready=mx_start_ready,
    close_when=mx_close_when,
    close_extract=mx_close_extract,
    close_ready=mx_close_ready
)


def fx_belongs(chunk: str) -> bool:
    return bool(chunk and chunk.strip())


def fx_start_when(payload: str) -> bool:
    return "Predict service" in payload or "正在生成汇总报告" in payload


def fx_start_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    last   = bucket.setdefault("_last", {})
    videos = bucket.setdefault("videos", [])

    if "视频尺寸" in payload:
        if m := SHAPE_RE.search(payload):
            last["shape"] = m.group().strip()
        return

    if "实际帧率" in payload:
        if m := REAL_FPS_RE.search(payload):
            last["real_fps"] = m.group().strip()
        return

    if "视频时长" in payload:
        if m := COST_RE.search(payload):
            last["cost"] = m.group().strip()
        return

    if "视频剪辑" in payload:
        last["clip"] = payload.split("视频剪辑:", 1)[1].strip()
        return

    if "视频过滤" in payload:
        if m := FILTER_RE.search(payload):
            tail  = m.group().strip()
            fps_m = FPS_RE.search(tail)
            name  = tail.split()[-1]
            item  = {
                "name"     : name,
                "fps"      : fps_m.group(1) if fps_m else "",
                "shape"    : last.get("shape", ""),
                "real_fps" : last.get("real_fps", ""),
                "cost"     : last.get("cost", ""),
                "clip"     : last.get("clip", "")
            }
            videos.append(item)
        return


def fx_start_ready(data: dict) -> bool:
    return bool(data.get("videos"))


def fx_close_when(payload: str) -> bool:
    return "跳过组合模式" in payload or "正在生成汇总报告" in payload


def fx_close_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if "Combine:" in payload:
        bucket["_comb"] = ""          # 缓冲
        bucket["_comb_on"] = True     # 开关
        return

    if not bucket.get("_comb_on"):
        return

    s = payload.strip()
    if not s:
        return

    looks_like_piece = (
        "Arkiv" in s or s.endswith((".ht", "ml", ".html"))
    )
    if not looks_like_piece:
        return

    bucket["_comb"] = bucket.get("_comb", "") + s

    if EXTRA_RE.search(bucket["_comb"]) and bucket["_comb"].lower().endswith(".html"):
        bucket["html"] = bucket["_comb"]
        bucket.pop("_comb_on", None)
        bucket.pop("_comb", None)


def fx_close_ready(payload: str) -> bool:
    return "Missions Done" in payload or "Missions Fail" in payload or "Missions Exit" in payload


FX_SPEC = GateSpec(
    name="framix",
    belongs=fx_belongs,
    start_when=fx_start_when,
    start_extract=fx_start_extract,
    start_ready=fx_start_ready,
    close_when=fx_close_when,
    close_extract=fx_close_extract,
    close_ready=fx_close_ready
)


if __name__ == '__main__':
    pass
