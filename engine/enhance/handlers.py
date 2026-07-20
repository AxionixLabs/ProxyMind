# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from loguru import logger
from mcp.types import CallToolResult
from mind_core.remote_services import RemoteServices
from mind_nova.requests.chat import stream_heal
from .fields import (
    fields,
    fields_map,
    tool_payload,
    tool_target
)
from .reporter import EnhanceReporter

async def enhance_result(
    *,
    pref_config: dict[str, typing.Any],
    name: str,
    result: CallToolResult,
    ok: bool,
    reporter: typing.Optional[EnhanceReporter] = None
) -> typing.Union[str, dict[str, typing.Any]]:
    """按工具名称增强成功结果。"""
    result_fields = fields(result)

    if not ok:
        return result_fields

    if name.startswith("nexus_"):
        return await enhance_nexus(result, reporter)

    if name == "heal_element":
        return await enhance_heal_element(result, pref_config, reporter)

    return result_fields


async def enhance_nexus(
    result: CallToolResult,
    reporter: typing.Optional[EnhanceReporter] = None
) -> typing.Union[str, dict[str, typing.Any]]:
    """Nexus: 全量静默落盘并返回原始 fields。"""
    result_fields = fields(result)

    if reporter and isinstance(result_fields, dict):
        await reporter.record(
            json.dumps(result_fields, ensure_ascii=False, indent=2) + "\n"
        )

    return result_fields


async def enhance_heal_element(
    result: CallToolResult,
    pref_config: dict[str, typing.Any],
    reporter: typing.Optional[EnhanceReporter] = None
) -> typing.Optional[dict[str, typing.Any]]:
    """调用远程元素自愈服务并汇总定位结果。"""
    result_fields = fields_map(result)

    attachments: list[dict[str, str]] = []

    heal_status = await RemoteServices.heal_license() or {}
    if not heal_status.get("enabled", False):
        return {
            "ok"          : False,
            "text"        : "Remote element healing service is unavailable.",
            "attachments" : attachments,
            "data"        : {"fields": result_fields}
        }

    payload = tool_payload(result_fields)
    if not payload:
        return {
            "ok"          : False,
            "text"        : "Device result is missing.",
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

        async for heal_event in stream_heal(pref_config, **data):
            event_type = heal_event.get("type")

            if event_type == "heal.step":
                message = str(heal_event.get("message") or "")
                if message:
                    if reporter:
                        await reporter.display(message)
                    else:
                        logger.debug(message)
                continue

            if event_type == "heal.failed":
                error = str(heal_event.get("error") or "unknown heal error")
                if reporter:
                    await reporter.display(error)
                else:
                    logger.debug(error)
                result_data.update({
                    "target" : target,
                    "error"  : heal_event.get("error")
                })
                continue

            if event_type != "heal.result":
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

            if reporter:
                await reporter.display(reason)
            return heal_locator, result_data

        return None, result_data

    if reporter:
        await reporter.begin_status()

    try:
        locator, heal_result_data = await collect_heal_locator()
    finally:
        if reporter:
            await reporter.end_status()

    if not locator:
        return {
            "ok"          : False,
            "text"        : "Element location failed.",
            "target"      : target,
            "attachments" : attachments,
            "data"        : heal_result_data
        }

    return {
        "ok"          : True,
        "text"        : "Element location succeeded.",
        "target"      : target,
        "attachments" : attachments,
        "data"        : heal_result_data
    }


if __name__ == '__main__':
    pass
