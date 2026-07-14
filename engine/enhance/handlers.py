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
    tool_payload,
    tool_target
)

async def enhance_result(
    *,
    pref_config: dict[str, typing.Any],
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

    if name == "heal_element":
        return await enhance_heal_element(result, pref_config, slog)

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


if __name__ == '__main__':
    pass
