# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
from loguru import logger
from mcp.types import (
    CallToolResult, TextContent
)
from backend.utilities.trace import summarize_args


def normalize_tool_output(raw_data: typing.Any) -> dict[str, typing.Any]:
    """把任意工具返回值归一化成统一结果结构。"""
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
            "data"        : raw_data.get("data") if "data" in raw_data else raw_data,
            "logs"        : raw_data.get("logs") or []
        }

    return {
        "text"        : None,
        "attachments" : [],
        "data"        : raw_data,
        "logs"        : []
    }


def build_call_tool_result(
    *,
    text: str,
    attachments: list[typing.Any] | None = None,
    data: dict[str, typing.Any] | None = None,
    is_error: bool = False,
    logs: list[typing.Any] | None = None
) -> CallToolResult:
    """按统一结构构造 MCP 工具返回对象。"""
    structured: dict[str, typing.Any] | None = {
        "text"        : text,
        "attachments" : attachments or [],
        "data"        : data or {}
    }
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=is_error,
        _meta={"logs": logs or []}
    )


async def broadcast(
    *,
    tool: str,
    args: dict,
    target_list: list,
    call: typing.Callable[[typing.Any, dict], typing.Awaitable[typing.Any]],
    overrides: typing.Optional[dict[str, dict]] = None
) -> CallToolResult:
    """
    并发执行多目标调用，并把结果汇总成统一的 MCP 返回结构。
    - call(agent, a)：每个 Agent 实际执行参数为 a（= 顶层 args + overrides[agent_key] 合并）
    - overrides：
        * None 或 {}：全量同参执行（所有 target 都跑，a= args）
        * 非空 dict：子集模式，只执行 overrides 中出现的 key 的设备；每台 a= args + overrides[key]
    - results[i]["args"]：保留该 Agent 最终 resolved_args，便于审计/复现
    """

    def key_of(target: typing.Any) -> str:
        """提取目标对象的稳定标识，用于分派和结果归档。"""
        return (
            getattr(target, "agent_id", None)
            or getattr(target, "serial", None)
            or str(target)
        )

    t0 = time.time()

    bases     = args or {}
    overrides = overrides or {}
    want      = set(overrides) if overrides else None

    targets = [
        target for target in target_list if (want is None or key_of(target) in want)
    ]
    keys = [key_of(target) for target in targets]
    resolved_args = [
        {**bases, **(overrides.get(k) or {})} for k in keys
    ]

    logger.debug(
        f"broadcast begin tool={tool} targets={keys} base_args={summarize_args(bases)} "
        f"override_keys={list(overrides.keys())}"
    )

    raw_list = await asyncio.gather(
        *(call(target, arg) for target, arg in zip(targets, resolved_args)),
        return_exceptions=True
    )

    done, fail = 0, 0
    results: list[dict[str, typing.Any]] = []
    attachments: list[dict[str, typing.Any]] = []

    for target, agent_id, resolved, raw in zip(targets, keys, resolved_args, raw_list):
        _ = target
        call_item: dict[str, typing.Any] = {
            "agent_id"    : agent_id,
            "ok"          : True,
            "args"        : resolved,
            "text"        : None,
            "attachments" : [],
            "data"        : None,
            "logs"        : []
        }

        if isinstance(raw, Exception):
            call_item["ok"] = False
            call_item["text"] = f"{type(raw).__name__}: {raw}"
            fail += 1
            logger.error(
                f"broadcast item tool={tool} agent_id={agent_id} ok=False "
                f"args={summarize_args(resolved)} error={call_item['text']}"
            )
        else:
            pack = normalize_tool_output(raw)
            data = pack.get("data")
            data_ok: typing.Optional[bool] = None

            if isinstance(data, dict) and "ok" in data:
                data_ok = bool(data.get("ok"))

            ok_this = data_ok if data_ok is not None else True
            call_item["ok"] = ok_this
            call_item["text"] = pack.get("text")
            call_item["attachments"] = pack.get("attachments") or []
            call_item["data"] = data
            call_item["logs"] = pack.get("logs") or []

            if ok_this:
                done += 1
            else:
                fail += 1

            level = logger.debug if ok_this else logger.warning
            level(
                f"broadcast item tool={tool} agent_id={agent_id} ok={ok_this} "
                f"args={summarize_args(resolved)}"
            )

        for attach in call_item["attachments"]:
            if isinstance(attach, dict):
                attachments.append({"agent_id": agent_id, **attach})
            else:
                attachments.append({"agent_id": agent_id, "kind": "unknown", "value": attach})

        results.append(call_item)

    total = len(targets)
    cost_ms = int((time.time() - t0) * 1000)

    lines: list[str] = [f"tool={tool} total={total} ok={done} fail={fail} elapsed_ms={cost_ms}"]
    for item in results:
        if item["ok"]:
            show = item.get("text", "") if item.get("text") is not None else str(item.get("data") or "")
            lines.append(f"agent_id={item['agent_id']} ok=True {show}")
        else:
            lines.append(f"agent_id={item['agent_id']} ok=False error={item['text']}")

    text = "\n".join(lines)
    data = {
        "ok"      : (fail == 0),
        "tool"    : tool,
        "args"    : args,
        "cost"    : cost_ms,
        "summary" : {"total": total, "done": done, "fail": fail},
        "results" : results
    }

    end_level = logger.debug if fail == 0 else logger.warning
    end_level(f"broadcast end tool={tool} total={total} ok={done} fail={fail} elapsed_ms={cost_ms}")

    return build_call_tool_result(
        text=text,
        attachments=attachments,
        data=data,
        is_error=not data["ok"],
        logs=[item.get("logs", []) for item in results]
    )


if __name__ == '__main__':
    pass
