#  _____           _ _
# |_   _|__   ___ | | |__   _____  __
#   | |/ _ \ / _ \| | '_ \ / _ \ \/ /
#   | | (_) | (_) | | |_) | (_) >  <
#   |_|\___/ \___/|_|_.__/ \___/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import time
import shutil
import typing
import asyncio
from mcp.types import (
    CallToolResult, TextContent
)
from engine.terminal import Terminal


async def port_listen(port: int, *, host: str = "127.0.0.1") -> bool:
    try:
        server = await asyncio.start_server(lambda r, w: None, host=host, port=port)
    except OSError:
        return False  # 已占用 / 无权限 / 不可绑定
    else:
        server.close()
        await server.wait_closed()
        return True   # 可绑定


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
            "ok"       : True,
            "data"     : None,
            "logs"     : []
        }
        if isinstance(raw, Exception):
            call_item["data"] = f"{type(raw).__name__}: {raw}"
            call_item["ok"]   = False
            fail += 1
        else:
            call_item["data"] = raw
            call_item["ok"]   = True
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

    lines: list[str] = [f"tool={tool} total={total} ok={done} fail={fail} elapsed_ms={cost_ms}"] + [
        f"agent_id={r['agent_id']} ok={r['ok']} data={r['data']}" for r in results
    ]

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent=structured,
        isError=(total > 0 and done < total),
        _meta={"logs": [r.get("logs", []) for r in results]}
    )


if __name__ == '__main__':
    pass


