# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mcp.types import CallToolResult
from mind_core.api import Api
from mind_nova import request
from mind_app.stream_ui import StreamUI
from .fields import (
    fields,
    fields_map,
    pref_slot_value,
    tool_payload,
    tool_target
)
from .uploads import upload_tool_result

if typing.TYPE_CHECKING:
    from mind_app.mcp import McpSessionLike


async def enhance_result(
    *,
    session: "McpSessionLike",
    mode: str,
    pref_config: dict[str, typing.Any],
    metadata: dict[str, typing.Any],
    name: str,
    result: CallToolResult,
    ok: bool,
    slog: typing.Optional[StreamUI] = None
) -> typing.Union[str, dict[str, typing.Any]]:
    """按工具名称增强成功结果。"""
    result_fields = fields(result)

    if not ok:
        return result_fields

    if name.startswith("nexus_"):
        return await enhance_nexus(result, slog)

    if name == "free_rule":
        return await enhance_free_rule(result, mode, pref_config, metadata, slog)

    if name in {
        "ffmpeg_extract_snapshot",
        "ffmpeg_extract_keyframes",
        "ffmpeg_extract_scene",
        "file_logcat_dump",
        "screenshot"
    }:
        return await enhance_artifact_upload(name, result)

    if name == "heal_element":
        return await enhance_heal_element(result, pref_config, slog)

    if name == "loop_steps":
        return await enhance_loop_steps(session, result, slog)

    return result_fields


async def enhance_nexus(
    result: CallToolResult,
    slog: typing.Optional[StreamUI] = None
) -> typing.Union[str, dict[str, typing.Any]]:
    """Nexus: 全量静默落盘并返回原始 fields。"""
    result_fields = fields(result)

    if slog and isinstance(result_fields, dict):
        await slog.feed(
            json.dumps(result_fields, ensure_ascii=False, indent=2) + "\n",
            echo=False,
            display=StreamUI.BLOCK
        )

    return result_fields


async def enhance_free_rule(
    result: CallToolResult,
    mode: str,
    pref_config: dict[str, typing.Any],
    metadata: dict[str, typing.Any],
    slog: typing.Optional[StreamUI] = None
) -> dict[str, typing.Any]:
    """执行自由规则并汇总各 agent 输出。"""
    result_fields = fields_map(result)

    attachments: list[dict[str, typing.Any]] = []

    payload = tool_payload(result_fields)
    if not payload:
        return {
            "ok"          : False,
            "text"        : "未获取到提示词（自由规则）的结果",
            "attachments" : attachments,
            "data"        : {}
        }

    if slog:
        await slog.open()

    try:
        message = payload.get("message")
        context = payload.get("context") or {}

        ok = True
        error: typing.Optional[dict[str, typing.Any]] = None
        chunks: list[str] = []
        async for rule_event in request.stream_rule(
            mode, pref_config, message, context, metadata
        ):
            if rule_event.get("type") == "turn.failed":
                ok = False
                error = rule_event
                continue
            if rule_event.get("type") not in {"text.delta", "text.done"}:
                continue

            chunk = str(rule_event.get("text") or "")
            if not chunk:
                continue
            chunks.append(chunk)
            if slog:
                await slog.feed(chunk, display_chunk=StreamUI.STREAM)

        return {
            "ok"          : ok,
            "text"        : "free rule completed",
            "attachments" : attachments,
            "data": {
                "mode"    : mode,
                "api"     : pref_slot_value(pref_config, "api"),
                "model"   : pref_slot_value(pref_config, "model"),
                "message" : message,
                "chunks"  : chunks,
                "error"   : error
            }
        }
    finally:
        if slog:
            await slog.stop()


async def enhance_artifact_upload(
    name: str,
    result: CallToolResult
) -> dict:
    """按工具名称选择附件上传配置。"""
    specs = {
        "ffmpeg_extract_snapshot": {
            "bucket"       : "frames",
            "missing_text" : "未获取到视频帧结果",
            "success_text" : "视频帧上传成功",
            "partial_text" : "视频帧上传完成（存在失败）"
        },
        "ffmpeg_extract_keyframes": {
            "bucket"       : "frames",
            "missing_text" : "未获取到视频帧结果",
            "success_text" : "视频帧上传成功",
            "partial_text" : "视频帧上传完成（存在失败）"
        },
        "ffmpeg_extract_scene": {
            "bucket"       : "frames",
            "missing_text" : "未获取到视频帧结果",
            "success_text" : "视频帧上传成功",
            "partial_text" : "视频帧上传完成（存在失败）"
        },
        "file_logcat_dump": {
            "bucket"       : "logcat",
            "missing_text" : "未获取到 logcat 结果",
            "success_text" : "logcat 上传成功",
            "partial_text" : "logcat 上传完成（存在失败）"
        },
        "screenshot": {
            "bucket"       : "screenshots",
            "missing_text" : "未获取到截图结果",
            "success_text" : "屏幕截图上传成功",
            "partial_text" : "屏幕截图上传完成（存在失败）"
        }
    }
    return await upload_tool_result(result, **specs[name])


async def enhance_heal_element(
    result: CallToolResult,
    pref_config: dict[str, typing.Any],
    slog: typing.Optional[StreamUI] = None
) -> typing.Optional[dict[str, typing.Any]]:
    """调用远程元素自愈服务并汇总定位结果。"""
    result_fields = fields_map(result)

    attachments: list[dict[str, str]] = []

    heal_status = await Api.heal_license() or {}
    if not heal_status.get("enabled", False):
        return {
            "ok"          : False,
            "text"        : "远程元素自愈服务暂不可用",
            "attachments" : attachments,
            "data"        : {"fields": result_fields}
        }

    payload = tool_payload(result_fields)
    if not payload:
        return {
            "ok"          : False,
            "text"        : "未获取到设备结果",
            "attachments" : attachments,
            "data"        : {"fields": result_fields}
        }

    target = tool_target(result_fields, payload)

    async def collect_heal_locator() -> tuple[
        typing.Optional[dict[str, typing.Any]],
        dict[str, typing.Any]
    ]:
        """收集单个目标的自愈定位结果。"""
        data = dict(payload)
        data.pop("serial", None)

        result_data: dict[str, typing.Any] = {}

        async for heal_event in request.stream_heal(pref_config, **data, slog=slog):
            if heal_event.get("type") == "heal.failed":
                result_data.update({
                    "target" : target,
                    "error"  : heal_event.get("error")
                })
                continue

            if heal_event.get("type") != "heal.result":
                continue

            heal_result = heal_event.get("result")
            if not isinstance(heal_result, dict):
                continue

            reason   = (heal_result.get("details") or {}).get("reason", "unknown")
            selector = ((heal_result.get("new_selector") or {}).get("primary") or {})

            heal_locator = {
                "by"    : selector.get("by"),
                "value" : selector.get("value")
            }
            result_data.update({
                "target"  : target,
                "locator" : heal_locator,
                "reason"  : reason
            })

            if slog:
                await slog.update_heal_status_summary(reason)
                await slog.feed(reason, display=StreamUI.BLOCK)
            return heal_locator, result_data

        return None, result_data

    if slog:
        await slog.begin_heal_status()

    try:
        locator, heal_result_data = await collect_heal_locator()
    finally:
        if slog:
            await slog.end_status()

    if not locator:
        return {
            "ok"          : False,
            "text"        : "元素定位失败",
            "target"      : target,
            "attachments" : attachments,
            "data"        : heal_result_data
        }

    return {
        "ok"          : True,
        "text"        : "元素定位成功",
        "target"      : target,
        "attachments" : attachments,
        "data"        : heal_result_data
    }


async def enhance_loop_steps(
    session: "McpSessionLike",
    result: CallToolResult,
    slog: typing.Optional[StreamUI] = None
) -> dict[str, typing.Any]:
    """执行 loop_steps 声明并汇总每轮步骤结果。"""

    async def say(line: str) -> None:
        """向流式界面输出 loop_steps 进度。"""
        if slog:
            return await slog.feed(line, display=StreamUI.BLOCK)

    result_fields = fields_map(result)

    payload = tool_payload(result_fields)
    if not payload:
        return {
            "ok"          : False,
            "text"        : "loop_steps: missing structured payload",
            "attachments" : [],
            "data"        : {},
            "logs"        : []
        }

    declaration_ok = bool(result_fields.get("ok")) if isinstance(result_fields.get("ok"), bool) else False

    if not declaration_ok:
        errs = payload.get("errors") or []
        text = (
            f"loop_steps: invalid declaration\n"
            f"errors={'; '.join([str(x) for x in errs[:8]])}" if errs else ""
        )
        return {
            "ok"          : False,
            "text"        : text,
            "attachments" : [],
            "data"        : payload | {"executed": False},
            "logs"        : []
        }

    loops = int(payload.get("loops", 1))
    steps = payload.get("steps", [])

    stop_on_fail = bool(payload.get("stop_on_fail", True))

    if not steps or not isinstance(steps, list):
        return {
            "ok"          : False,
            "text"        : "loop_steps: empty steps",
            "attachments" : [],
            "data"        : payload | {"executed": False},
            "logs"        : []
        }

    if any(isinstance(step, dict) and (step.get("tool") == "loop_steps") for step in steps):
        return {
            "ok"          : False,
            "text"        : "loop_steps: nested loop_steps forbidden (runner guard)",
            "attachments" : [],
            "data"        : payload | {"executed": False},
            "logs"        : []
        }

    attachments: list[dict[str, typing.Any]] = []
    runs: list[dict[str, typing.Any]] = []

    if slog:
        await slog.end_status()
        await slog.begin_loop_status()
    await say(
        f"loop_steps: begin loops={loops} steps={len(steps)} stop_on_fail={stop_on_fail}"
    )

    for r in range(loops):
        if slog:
            await slog.update_loop_status_summary(f"round {r + 1}/{loops}")

        await say(f"loop_steps: round {r + 1}/{loops}")
        round_ok = True

        round_steps: list[dict[str, typing.Any]] = []

        for i, step in enumerate(steps):
            tool = (step.get("tool") or "").strip()
            args = step.get("args") if isinstance(step.get("args"), dict) else {}

            if slog:
                await slog.update_loop_status_summary(
                    f"round {r + 1}/{loops} step {i + 1}/{len(steps)}"
                )
            await say(f"loop_steps:  step {i + 1}/{len(steps)} tool={tool}")

            tool_res = await session.call_tool(tool, args)
            ok = (not tool_res.isError)

            step_fields = fields(tool_res)

            if isinstance(step_fields, dict):
                atts = step_fields.get("attachments")
                if atts and isinstance(atts, list):
                    attachments.extend(atts)

            round_steps.append({
                "ok": ok, "tool": tool, "args": args, "fields": step_fields
            })

            if not ok:
                round_ok = False
                await say(f"loop_steps:  step failed tool={tool}")
                if stop_on_fail:
                    break

            await say(
                f"loop_steps:  step {i + 1}/{len(steps)} tool={tool} "
                f"{step_fields.get('text') if isinstance(step_fields, dict) else str(step_fields)}"
            )

        runs.append({"round": r + 1, "ok": round_ok, "steps": round_steps})
        if slog:
            await slog.update_loop_status_summary(
                f"round {r + 1}/{loops} {'done' if round_ok else 'failed'}"
            )
        if stop_on_fail and not round_ok:
            await say(f"loop_steps: stop (round {r + 1} failed)")
            break

    final_ok = bool(runs) and all(x.get("ok") for x in runs)

    brief = [
        f"tool=loop_steps ok={final_ok} rounds={len(runs)}/{loops} stop_on_fail={stop_on_fail}"
    ]
    for run in runs:
        if run.get("ok"):
            continue
        for step in run.get("steps", []):
            if not step.get("ok"):
                brief.append(
                    f"fail round={run['round']} tool={step.get('tool')}"
                )

    try:
        return {
            "ok"          : final_ok,
            "text"        : "\n".join(brief),
            "attachments" : attachments,
            "data": {
                "executed"     : True,
                "loops"        : loops,
                "steps"        : steps,
                "stop_on_fail" : stop_on_fail,
                "runs"         : runs
            },
            "logs": []
        }
    finally:
        if slog:
            await slog.end_status()


if __name__ == '__main__':
    pass
