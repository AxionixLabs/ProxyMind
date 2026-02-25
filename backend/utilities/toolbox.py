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
    call: typing.Callable[[typing.Any, dict], typing.Awaitable[typing.Any]],
    overrides: typing.Optional[dict[str, dict]] = None
) -> CallToolResult:
    """
    - call(agent, a)：每个 Agent 实际执行参数为 a（= 顶层 args + overrides[agent_key] 合并）
    - overrides：
        * None 或 {}：全量同参执行（所有 target 都跑，a= args）
        * 非空 dict：子集模式，只执行 overrides 中出现的 key 的设备；每台 a= args + overrides[key]
    - results[i]["args"]：保留该 Agent 最终 resolved_args，便于审计/复现
    """

    def key_of(target: typing.Any) -> str:
        """每个 target 的唯一键：优先 agent_id，其次 serial，最后稳定兜底。"""
        return (
            getattr(target, "agent_id", None)
            or getattr(target, "serial", None)
            or str(target)
        )

    def normalize(raw_data: typing.Any) -> dict[str, typing.Any]:
        """归一化单个 agent 返回为 {text,attachments,data,logs}。"""
        if raw_data is None:
            return {
                "text"        : None,
                "attachments" : [],
                "data"        : None,
                "logs"        : []
            }

        if isinstance(raw_data, Exception):
            return {
                "text"        : f"{type(raw_data).__name__}: {raw_data}",
                "attachments" : [],
                "data"        : None,
                "logs"        : []
            }

        if isinstance(raw_data, str):
            return {
                "text"        : raw_data,
                "attachments" : [],
                "data"        : None,
                "logs"        : []
            }

        if isinstance(raw_data, dict):
            return {
                "text"        : raw_data.get("text"),
                "attachments" : raw_data.get("attachments") or [],
                "data"        : raw_data.get("data") if "data" in raw_data else raw_data,  # 兼容：没 data 就把整包当 data
                "logs"        : raw_data.get("logs") or []
            }

        # 其他类型：按 data 返回
        return {
            "text"        : None,
            "attachments" : [],
            "data"        : raw_data,
            "logs"        : []
        }

    t0 = time.time()
    bases = args or {}
    overrides = overrides or {}

    # 规则：overrides 非空 => 子集模式（只跑 overrides keys）；否则全量同参
    want = set(overrides) if overrides else None

    # 过滤出本次真正要执行的 targets（want=None => 全量）
    targets = [
        target for target in target_list if (want is None or key_of(target) in want)
    ]
    keys = [key_of(target) for target in targets]

    # 计算每个 Agent 最终参数：a = args + overrides[key]
    resolved_args = [
        {**bases, **(overrides.get(k) or {})} for k in keys
    ]

    # 并发执行：每个 Agent 把 resolved_args 交给 call(agent, a)
    raw_list = await asyncio.gather(
        *(call(target, arg)
          for target, arg in zip(targets, resolved_args)), return_exceptions=True
    )

    done, fail, results, attachments = 0, 0, [], []

    # 逐个 Agent 汇总：把 resolved args 固化到 results[i]["args"]
    for target, k, a, raw in zip(targets, keys, resolved_args, raw_list):
        agent_id = k

        call_item: dict[str, typing.Any] = {
            "agent_id"    : agent_id,
            "ok"          : True,
            "args"        : a,
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
            pack = normalize(raw)

            # 优先使用 data.ok 作为成功口径；没有 data.ok 才退回“非异常=成功”
            data_ok: typing.Optional[bool] = None

            if isinstance(data := pack.get("data"), dict) and "ok" in data:
                data_ok = bool(data.get("ok"))

            ok_this = (data_ok if data_ok is not None else True)

            call_item["ok"]          = ok_this
            call_item["text"]        = pack.get("text")
            call_item["attachments"] = pack.get("attachments") or []
            call_item["data"]        = data
            call_item["logs"]        = pack.get("logs") or []

            if ok_this: done += 1
            else: fail += 1

        # 汇总附件：带上 agent_id 方便定位来源
        for attach in call_item["attachments"]:
            if isinstance(attach, dict):
                attachments.append({"agent_id": agent_id, **attach})
            else:
                attachments.append({"agent_id": agent_id, "kind": "unknown", "value": attach})

        results.append(call_item)

    total   = len(targets)
    cost_ms = int((time.time() - t0) * 1000)

    lines: list[str] = [f"tool={tool} total={total} ok={done} fail={fail} elapsed_ms={cost_ms}"]
    for r in results:
        if r["ok"]:
            show = r.get("text", "") if r.get("text") is not None else str(r.get("data") or "")
            lines.append(f"agent_id={r['agent_id']} ok=True {show}")
        else:
            lines.append(f"agent_id={r['agent_id']} ok=False error={r['text']}")

    structured: typing.Optional[dict[str, typing.Any]] = {
        "text"        : "\n".join(lines),
        "attachments" : attachments,
        "data": {
            "ok"      : (fail == 0),
            "tool"    : tool,
            "args"    : args,
            "cost"    : cost_ms,
            "summary" : {"total": total, "done": done, "fail": fail},
            "results" : results
        }
    }

    return CallToolResult(
        content=[TextContent(type="text", text=structured["text"])],
        structuredContent=structured,
        isError=not structured["data"]["ok"],
        _meta={"logs": [r.get("logs", []) for r in results]}
    )


if __name__ == '__main__':
    pass
