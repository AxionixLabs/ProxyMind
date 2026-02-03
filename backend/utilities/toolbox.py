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

    def normalize() -> dict[str, typing.Any]:
        """
        归一化单个 agent 返回为：
        {
          "text"        : str|None,
          "attachments" : list,
          "data"        : any,
          "logs"        : list
        }
        """
        if raw is None:
            return {"text": None, "attachments": [], "data": None, "logs": []}

        if isinstance(raw, str):
            return {"text": raw, "attachments": [], "data": None, "logs": []}

        if isinstance(raw, dict):
            return {
                "text"        : raw.get("text"),
                "attachments" : raw.get("attachments") or [],
                "data"        : raw.get("data") if "data" in raw else raw,  # 兼容：没 data 就把整包当 data
                "logs"        : raw.get("logs") or []
            }

        # 其他类型：按 data 返回
        return {"text": None, "attachments": [], "data": raw, "logs": []}

    t0 = time.time()

    raw_list = await asyncio.gather(
        *(call(target) for target in target_list), return_exceptions=True
    )

    done, fail, results, attachments = 0, 0, [], []

    for target, raw in zip(target_list, raw_list):
        agent_id = getattr(target, "agent_id", None) or str(target)

        call_item: dict[str, typing.Any] = {
            "agent_id"    : agent_id,
            "ok"          : True,
            "text"        : None,
            "attachments" : [],
            "data"        : None,
            "logs"        : []
        }
        if isinstance(raw, Exception):
            call_item["ok"]   = False
            call_item["text"] = f"{type(raw).__name__}: {raw}"
            call_item["data"] = None
            fail += 1
        else:
            pack = normalize()
            call_item["ok"]          = True
            call_item["text"]        = pack.get("text")
            call_item["attachments"] = pack.get("attachments") or []
            call_item["data"]        = pack.get("data")
            call_item["logs"]        = pack.get("logs") or []
            done += 1

        # 汇总附件：带上 agent_id 方便定位来源
        for attach in call_item["attachments"]:
            if isinstance(attach, dict):
                attachments.append({"agent_id": agent_id, **attach})
            else:
                attachments.append({"agent_id": agent_id, "kind": "unknown", "value": attach})

        results.append(call_item)

    total   = len(target_list)
    cost_ms = int((time.time() - t0) * 1000)

    lines: list[str] = [f"tool={tool} total={total} ok={done} fail={fail} elapsed_ms={cost_ms}"]
    for r in results:
        if r["ok"]:
            show = r.get("text", "") if r.get("text") is not None else str(r.get("data") or "")
            lines.append(f"agent_id={r['agent_id']} ok=True {show}")
        else:
            lines.append(f"agent_id={r['agent_id']} ok=False error={r['text']}")

    structured: typing.Optional[dict[str, typing.Any]] = {
        "tool" : tool,
        "args" : args,
        "cost" : cost_ms,
        "summary" : {
            "total" : total,
            "done"  : done,
            "fail"  : fail
        },
        "text"        : "\n".join(lines),
        "attachments" : attachments,
        "results"     : results
    }

    return CallToolResult(
        content=[TextContent(type="text", text=structured["text"])],
        structuredContent=structured,
        isError=(total > 0 and done < total),
        _meta={"logs": [r.get("logs", []) for r in results]}
    )


if __name__ == '__main__':
    pass


