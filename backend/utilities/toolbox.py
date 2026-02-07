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
from backend.mcp_hub.hub_manage import DeviceManage
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


def pick_plan(manage: DeviceManage, plan: dict) -> tuple[list, dict[str, dict]]:
    """
    从 CTX plan 中解析 targets/overrides，并基于 manage.snapshot 得到：
      - target_list: 实际要执行的设备列表
      - ov: 仅保留 targets 子集内的 overrides（value 必须是 dict）
    约定：
      - targets is None -> 单控：返回全量 snapshot，ov={}
      - targets 是 list[str] -> 子集群控
    """
    targets      = plan.get("targets")          # None | list[str]
    overrides    = plan.get("overrides") or {}  # dict[str, dict]
    base_targets = manage.snapshot

    if targets is None:
        return base_targets, {}

    want = set(targets)
    target_list = [a for a in base_targets if getattr(a, "agent_id", None) in want]

    if not isinstance(overrides, dict):
        return target_list, {}

    ov = {str(k): v for k, v in overrides.items() if str(k) in want and isinstance(v, dict)}
    return target_list, ov


async def broadcast(
    *,
    tool: str,
    args: dict,
    target_list: list,
    call: typing.Callable[[typing.Any, dict], typing.Awaitable[typing.Any]],
    overrides: typing.Optional[dict[str, dict]] = None
) -> CallToolResult:

    def normalize() -> dict[str, typing.Any]:
        if raw is None:
            return {"text": None, "attachments": [], "data": None, "logs": []}
        if isinstance(raw, str):
            return {"text": raw, "attachments": [], "data": None, "logs": []}
        if isinstance(raw, dict):
            return {
                "text"        : raw.get("text"),
                "attachments" : raw.get("attachments") or [],
                "data"        : raw.get("data") if "data" in raw else raw,
                "logs"        : raw.get("logs") or []
            }
        return {"text": None, "attachments": [], "data": raw, "logs": []}

    t0 = time.time()
    
    overrides = overrides or {}
    
    agents, arg_list = [], []

    for target in target_list:
        agent_id = getattr(target, "agent_id", None) or str(target)
        agents.append(agent_id)

        ov = overrides.get(agent_id) or {}
        if not isinstance(ov, dict):
            ov = {}

        arg_list.append({**(args or {}), **ov})

    raw_list = await asyncio.gather(
        *(call(target, a) for target, a in zip(target_list, arg_list)), return_exceptions=True
    )

    done, fail, results, attachments = 0, 0, [], []

    for target, agent_id, arg, raw in zip(target_list, agents, arg_list, raw_list):
        call_item: dict[str, typing.Any] = {
            "agent_id"    : agent_id,
            "ok"          : True,
            "args"        : arg,
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
        "summary": {
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


