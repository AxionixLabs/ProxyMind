#  ____  _            _ _
# |  _ \(_)_ __   ___| (_)_ __   ___
# | |_) | | '_ \ / _ \ | | '_ \ / _ \
# |  __/| | |_) |  __/ | | | | |  __/
# |_|   |_| .__/ \___|_|_|_| |_|\___|
#         |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import time
import random
import shutil
import typing
import asyncio
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from mcp.types import (
    CallToolResult, TextContent
)
from engine.terminal import Terminal
from backend.utilities import const


class _HelixBaseError(BaseException):
    """_HelixBaseError class."""
    pass


class HelixError(_HelixBaseError):
    """HelixError class."""

    def __init__(self, msg: typing.Any):
        self.msg = msg

    def __str__(self):
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    console: Console = Console()

    class _RichSink(RichHandler):
        debug_color = [
            "#00CED1",  # 深青色 - 冷静理性
            "#98FB98",  # 浅绿色 - 绿色无压调试层
            "#B0C4DE",  # 灰蓝色 - 安静辅助信息
        ]
        info_color = [
            "#D8BFD8",  # 藕荷紫 - 精致低饱和
            "#EEE8AA",  # 浅卡其 - 稳妥类日志色
            "#F0FFF0",  # 蜜瓜白 - 极淡提示背景色
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
            Active._RichSink(Active.console), level=log_level, format=const.PRINT_FORMAT
        )


async def kill_port(port: int) -> typing.Any:
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh: return None
        cmd = [
            pwsh, "-Command", "Get-NetTCPConnection", "-LocalPort", f"{port}",
            "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
        ]
    else:
        cmd = ["lsof", "-ti", f":{port}", "|", "xargs", "kill", "-9"]

    return await Terminal.cmd_line(cmd)


async def broadcast(
    *,
    tool: str,
    args: dict,
    target_list: list,
    call: typing.Callable[[typing.Any], typing.Awaitable[typing.Any]]
) -> CallToolResult:

    t0 = time.time()

    raw_list = await asyncio.gather(
        *(call(target) for target in target_list), return_exceptions=True
    )

    done, fail, results = 0, 0, []

    for target, raw in zip(target_list, raw_list):
        call_item: dict[str, typing.Any] = {
            "agent_id" : getattr(target, "agent_id", None) or str(target),
            "data"     : None,
            "logs"     : []
        }
        if isinstance(raw, Exception):
            call_item["data"] = f"{type(raw).__name__}: {raw}"
            fail += 1
        else:
            call_item["data"] = raw
            done += 1
        results.append(call_item)

    structured: typing.Optional[dict[str, typing.Any]] = {
        "tool" : tool,
        "args" : args,
        "cost" : (cost_ms := int((time.time() - t0) * 1000)),
        "summary" : {
            "total" : (total := len(target_list)),
            "done"  : done,
            "fail"  : fail
        },
        "results" : results
    }

    lines: list[str] = [f"{tool} done={done}/{total} fail={fail} cost_ms={cost_ms}"] + [
        f"{result['agent_id']} | {result['data']}" for result in results
    ]

    is_error = (total > 0 and done < total)

    _meta = {
        "logs": [r.pop("logs", []) for r in results]
    }

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent=structured,
        isError=is_error,
        _meta=_meta
    )


if __name__ == '__main__':
    pass
