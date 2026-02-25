#  _____ _       _
# |_   _(_)_ __ | | _____ _ __
#   | | | | '_ \| |/ / _ \ '__|
#   | | | | | | |   <  __/ |
#   |_| |_|_| |_|_|\_\___|_|
#

import os
import sys
import json
import random
import shutil
import typing
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from engine.terminal import Terminal
from mindcore.design import (
    Design, TypewriterStreamSession
)
from mindnova import const


class _MindBaseError(BaseException):
    """_MindBaseError class."""
    pass


class MindError(_MindBaseError):
    """MindError class."""

    def __init__(self, msg: typing.Any):
        self.msg = msg

    def __str__(self):
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    class _RichSink(RichHandler):
        debug_color = [
            "#00E5FF",  # 电青
            "#39FF14",  # 霓虹绿
            "#FF2D95",  # 霓虹粉
        ]
        info_color = [
            "#FFD300",  # 电黄
            "#7CFF6B",  # 亮绿
            "#64748B",  # 蓝灰
        ]
        level_style = {
            "DEBUG"    : f"bold {random.choice(debug_color)}",
            "INFO"     : f"bold {random.choice(info_color)}",
            "WARNING"  : "bold #FFD700",
            "ERROR"    : "bold #FF4500",
            "CRITICAL" : "bold #FF1493",
        }

        def __init__(self, console: "Console"):
            super().__init__(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                markup=False
            )

        def emit(self, record: "LogRecord") -> None:
            self.console.print(
                const.PRINT_HEAD, Text(self.format(record), style=self.level_style.get(
                    record.levelname, "bold #ADD8E6"
                ))
            )

    @staticmethod
    def active(log_level: str) -> None:
        logger.remove()
        logger.add(
            Active._RichSink(Design.console), level=log_level, format=const.PRINT_FORMAT
        )


class FileAssist(object):
    """FileAssist class."""

    @staticmethod
    async def open(file: str) -> typing.Optional[str]:
        if sys.platform == "win32":
            cmd = ["notepad++"] if shutil.which("notepad++") else ["Notepad"]
        else:
            cmd = ["open", "-W", "-a", "TextEdit"]
        return await Terminal.cmd_line(cmd + [file])

    @staticmethod
    def read_json(file: str) -> dict:
        with open(file, "r", encoding=const.CHARSET) as f:
            return json.loads(f.read())

    @staticmethod
    def dump_json(src: str, dst: dict) -> None:
        with open(src, "w", encoding=const.CHARSET) as f:
            f.write(json.dumps(dst, indent=4, separators=(",", ":"), ensure_ascii=False))


class Tooling(object):

    @staticmethod
    def filter_tools(
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
        domains: typing.Optional[typing.Iterable[str]] = None,
        classes: typing.Optional[typing.Iterable[str]] = None,
        *,
        include_hidden: bool = False,
        exclude: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> list[dict[str, typing.Any]]:
        """
        - domains/classes: allowlist（AND 叠加：传哪个就按哪个过滤）
        - exclude: 排除规则列表；每条规则是 AND 匹配（命中就剔除）
          支持键：domain / class / name
          例：exclude=[{"domain":"media","class":"scrcpy"}]
        """
        want_domain = {
            str(d).strip() for d in domains if str(d).strip()
        } if domains else None

        want_class = {
            str(c).strip() for c in classes if str(c).strip()
        } if classes else None

        exclude = exclude or []

        out: list[dict[str, typing.Any]] = []

        for item in openai_tools:
            func = (item or {}).get("function") or {}
            name = func.get("name")
            if not name: continue

            meta = tool_meta.get(name) or {}

            if not include_hidden and bool(meta.get("hidden", False)):
                continue

            if want_domain is not None and meta.get("domain") not in want_domain:
                continue

            if want_class is not None and meta.get("class") not in want_class:
                continue

            # 排除规则：每条规则内部是 AND
            hit_exclude = False
            for rule in exclude:
                if not isinstance(rule, dict):
                    continue
                ok = True
                if "name" in rule:
                    ok = ok and (name == rule["name"])
                if "domain" in rule:
                    ok = ok and (meta.get("domain") == rule["domain"])
                if "class" in rule:
                    ok = ok and (meta.get("class") == rule["class"])
                if ok:
                    hit_exclude = True
                    break

            if hit_exclude:
                continue
            out.append(item)

        return out

    @staticmethod
    def require(
        meta_map: dict[str, dict[str, typing.Any]],
        name: str,
        *,
        domain_in: typing.Optional[typing.Container[str]] = None,
        class_in: typing.Optional[typing.Container[str]] = None,
        name_in: typing.Optional[typing.Container[str]] = None,
        name_not_in: typing.Optional[typing.Container[str]] = None,
        class_not_in: typing.Optional[typing.Container[str]] = None
    ) -> bool:
        """
        判断某工具是否需要“连接/设备准备”等前置动作。

        规则：
        1) 先排除：name ∈ name_not_in 或 meta.class ∈ class_not_in => False
        2) 再命中：name_in / domain_in / class_in 任一命中 => True（OR）
        3) 都不传：默认（domain=device 或 class=scrcpy）
        """

        meta = meta_map.get(name) or {}
        dom  = meta.get("domain", "")
        cls  = meta.get("class", "")

        if (name_not_in and name in name_not_in) or (class_not_in and cls in class_not_in):
            return False

        if domain_in is None and class_in is None and name_in is None:
            domain_in, class_in = {"device"}, {"scrcpy"}

        return bool(
            (name_in and name in name_in)
            or (domain_in and dom in domain_in)
            or (class_in and cls in class_in)
        )


class StreamTyperLogger(object):
    """
    打字机 + 流式转录日志（只写“行内容”，不走 logger，不影响控制台）
    - feed(text): 显示到打字机，同时按行写入文件（原样）
    - flush(): 把残留半行写入
    """

    def __init__(
        self,
        log_file: str,
        tw: typing.Optional[TypewriterStreamSession] = None
    ) -> None:

        self.tw = tw or TypewriterStreamSession()
        self.log_file = log_file
        self.buffer: str = ""
        self.fp: typing.Optional[typing.TextIO] = None

    async def start(self) -> None:
        await self.tw.start()
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self.fp = open(
            self.log_file, "a", encoding=const.CHARSET, buffering=1, newline=""
        )

    async def stop(self) -> None:
        self.flush()
        await self.tw.stop()
        if self.fp:
            try:
                self.fp.flush()
            finally:
                self.fp.close()
            self.fp = None

    async def feed(self, chunk: typing.Optional[str]) -> None:
        if not chunk: return None

        text = str(chunk)

        await self.tw.feed(text)

        self.buffer += text
        self.drain()

    def flush(self) -> None:
        if self.buffer:
            self.write(self.buffer)
            self.buffer = ""

    def drain(self) -> None:
        while True:
            if (pos := self.buffer.find("\n")) < 0:
                break
            line = self.buffer[:pos + 1]
            self.buffer = self.buffer[pos + 1:]
            self.write(line)

    def write(self, s: str) -> None:
        if not self.fp: return None
        self.fp.write(s)


if __name__ == '__main__':
    pass
