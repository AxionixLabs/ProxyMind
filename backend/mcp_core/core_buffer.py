#   ____                 ____         __  __
#  / ___|___  _ __ ___  | __ ) _   _ / _|/ _| ___ _ __
# | |   / _ \| '__/ _ \ |  _ \| | | | |_| |_ / _ \ '__|
# | |__| (_) | | |  __/ | |_) | |_| |  _|  _|  __/ |
#  \____\___/|_|  \___| |____/ \__,_|_| |_|  \___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
from dataclasses import (
    dataclass, field
)


@dataclass
class GateSpec:
    name: str
    # 是否属于该工具的日志行（用于过滤）
    belongs: typing.Callable[[str], bool]

    # 触发条件：start/tail/done
    start_when: typing.Callable[[str], bool]
    tail_when: typing.Callable[[str], bool]
    done_when: typing.Callable[[str], bool]

    # 抽取逻辑：start/tail 各自怎么从一行里抽字段
    start_extract: typing.Callable[[dict, str], None]
    tail_extract: typing.Callable[[dict, str], None]

    # start 阶段什么时候“齐了”可以发事件（可选）
    start_ready: typing.Callable[[dict], bool] = lambda x: True
    # tail 阶段什么时候“齐了”可以发 end（通常 done_when 才发；你也可以用这个）
    tail_ready: typing.Callable[[dict], bool] = lambda x: True


@dataclass
class GateState:
    mode: str = "seek_start"  # seek_start|start|running|tail|done
    start: dict = field(default_factory=dict)
    end: dict = field(default_factory=dict)
    start_emitted: bool = False


class LineBuffer(object):

    buffer: str = ""

    def __init__(self):
        self.buffer = ""

    def feed(self, chunk: str) -> list[str]:
        """
        输入任意文本 chunk（可能包含多行、也可能半行）
        产出：所有“完整行”（不含行尾换行符）
        未结束的半行留在 buf 里等待下一次 chunk
        """
        self.buffer += chunk
        lines = self.buffer.splitlines(keepends=True)

        out = []
        rest = []
        for line in lines:
            if line.endswith("\n") or line.endswith("\r"):
                out.append(line.rstrip("\r\n"))
            else:
                rest.append(line)

        self.buffer = "".join(rest)
        return out

    def flush(self) -> list[str]:
        """流结束时把最后的半行吐出来（如果有）"""
        if self.buffer:
            last = self.buffer
            self.buffer = ""
            return [last]
        return []


class Pick(object):

    @staticmethod
    def pick_value(line: str) -> typing.Optional[str]:
        """取 " | " 后面的内容。"""
        if " | " not in line:
            return None
        return line.split(" | ", 1)[1].strip()

    @staticmethod
    def pick_arrow(line: str) -> typing.Optional[str]:
        """取 "->" 后面的内容。"""
        if "->" not in line:
            return None
        return line.split("->", 1)[1].strip()


class GateMachine(object):

    def __init__(self, spec: GateSpec):
        self.spec = spec
        self.state = GateState()

    @staticmethod
    def call_ready(fn: typing.Callable, bucket: dict) -> bool:
        try:
            return bool(fn(bucket))  # 1参版本
        except TypeError:
            return bool(fn())  # 0参版本

    def feed_line(self, line: str) -> typing.Optional[dict]:
        """
        输入：单行日志（已由上游做过 chunk->line 的拼接）
        输出：
          - None：本行不产生阶段事件
          - dict：产生阶段事件（phase=start/end），可用于打印/回灌/return
        """

        # 去掉“前缀|”后的内容（更干净、匹配更稳）
        payload = Pick.pick_value(line) or ""

        # 多工具并行/多源(stdout/stderr)时避免互相污染
        if not self.spec.belongs(line):
            return None

        state = self.state

        # [Stage 1] seek_start：尚未观察到“开始信号”
        if state.mode == "seek_start":
            if self.spec.start_when(payload):
                # 命中开始信号 -> 进入 start 阶段（开始收集 start 字段）
                state.mode = "start"
            return None

        # [Stage 2] start：开始阶段（收集开始摘要）
        if state.mode == "start":
            self.spec.start_extract(state.start, payload)

            # start_emitted：保证 start 事件只发一次（即使后续又出现类似字段）
            if (not state.start_emitted) and self.call_ready(self.spec.start_ready, state.start):
                state.start_emitted = True

                # start 结束，进入 running（运行期默认丢弃过程日志）
                state.mode = "running"
                return {"phase": "start", "tool": self.spec.name, **state.start}

            # 命中 tail_when 则直接切到 tail，避免漏掉 end
            if self.spec.tail_when(payload):
                state.mode = "tail"
            return None

        # [Stage 3] running：运行期（过程阶段）
        if state.mode == "running":
            if self.spec.tail_when(payload):
                # 命中收尾信号 -> 进入 tail 阶段（开始收集 end 字段）
                state.mode = "tail"
            return None

        # [Stage 4] tail：收尾阶段（收集结束摘要）
        if state.mode == "tail":
            self.spec.tail_extract(state.end, payload)
            if self.spec.done_when(payload):
                # 结束 -> 进入 done（后续行不再产出）
                state.mode = "done"
                return {"phase": "end", "tool": self.spec.name, **state.end}
            return None

        # [Stage 5] done：已结束
        return None


def mx_belongs(line: str) -> bool:
    return "Memrix ::" in line


def mx_start_when(payload: str) -> bool:
    return "Engine Start" in payload or "Report Start" in payload


def mx_tail_when(payload: str) -> bool:
    return "Finishing up" in payload or "Score" in payload


def mx_done_when(payload: str) -> bool:
    return "Engine Close" in payload or "Report Close" in payload


def mx_start_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if payload.startswith("时间"):
        bucket["time"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("应用"):
        bucket["app"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("频率"):
        bucket["freq"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("标签"):
        bucket["tag"] = Pick.pick_arrow(payload) or payload
    elif payload.startswith("Token:"):
        bucket["token"] = payload.split("Token:", 1)[1].strip()


def mx_tail_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if "Library\\Tree" in payload or "Library/Tree" in payload:
        bucket["report"] = payload
    elif payload.startswith("Usage:"):
        if m := re.search(r"(?<=--forge/s).*", payload):
            bucket["usage"] = m.group()


def mx_start_ready(d: dict) -> bool:
    return all(k in d for k in ("time", "app", "freq", "file", "token"))


MX_SPEC = GateSpec(
    name="memrix",
    belongs=mx_belongs,
    start_when=mx_start_when,
    tail_when=mx_tail_when,
    done_when=mx_done_when,
    start_extract=mx_start_extract,
    tail_extract=mx_tail_extract,
    start_ready=mx_start_ready
)


def fx_belongs(line: str) -> bool:
    return "Framix :: " in line


def fx_start_when(payload: str) -> bool:
    return "Predict service" in payload


def fx_tail_when(payload: str) -> bool:
    return "正在生成汇总报告" in payload or "跳过组合模式" in payload


def fx_done_when(payload: str) -> bool:
    return "Missions Done" in payload or "Missions Fail" in payload or "Missions Exit" in payload


def fx_start_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if "视频尺寸" in payload:
        bucket["shape"] = payload
    elif "实际帧率" in payload:
        bucket["fps"] = payload
    elif "视频时长" in payload:
        bucket["time"] = payload
    elif "正在生成汇总报告" in payload:  # TODO
        bucket["reporter_start"] = ""


def fx_tail_extract(bucket: dict[str, typing.Any], payload: str) -> None:
    if "开始帧" in payload:
        bucket["analyzer_close"] = payload

    elif "Combine:" in payload:
        if m := re.search(r"(?<=Combine:\s).*", payload):
            bucket["reporter_close"] = m.group()


FX_SPEC = GateSpec(
    name="framix",
    belongs=fx_belongs,
    start_when=fx_start_when,
    tail_when=fx_tail_when,
    done_when=fx_done_when,
    start_extract=fx_start_extract,
    tail_extract=fx_tail_extract,
    start_ready=lambda x: True
)


if __name__ == '__main__':
    pass
