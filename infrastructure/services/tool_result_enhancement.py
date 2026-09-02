# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from infrastructure.services.remote_services import RemoteServices
from observability import observe
from protocol.client.heal import stream_heal


class ResultEnhancementReporter(typing.Protocol):
    """描述远端结果增强过程需要的展示和状态端口。"""

    async def display(self, text: str) -> None:
        """展示增强过程产生的文本。"""
        ...

    async def begin_status(self) -> None:
        """启动增强过程状态。"""
        ...

    async def end_status(self) -> None:
        """结束增强过程状态。"""
        ...


def _tool_payload(result_fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取单次工具结果中的业务载荷。"""
    data = result_fields.get("data")
    return data if isinstance(data, dict) else {}


def _tool_target(
    result_fields: dict[str, typing.Any],
    payload: dict[str, typing.Any],
) -> str:
    """提取单次工具结果对应的目标标识。"""
    target = (
        result_fields.get("target")
        or payload.get("serial")
        or result_fields.get("tool")
    )
    return str(target or "default")


async def enhance_tool_result(
    *,
    pref_config: dict[str, typing.Any],
    name: str,
    result_fields: dict[str, typing.Any],
    ok: bool,
    reporter: ResultEnhancementReporter | None = None,
) -> dict[str, typing.Any]:
    """按工具名称增强成功结果。"""
    if not ok or name != "heal_element":
        return result_fields
    return await _enhance_heal_element(result_fields, pref_config, reporter)


async def _enhance_heal_element(
    result_fields: dict[str, typing.Any],
    pref_config: dict[str, typing.Any],
    reporter: ResultEnhancementReporter | None,
) -> dict[str, typing.Any]:
    """调用远程元素自愈服务并汇总定位结果。"""
    attachments: list[dict[str, str]] = []
    heal_status = await RemoteServices.heal_license() or {}
    if not heal_status.get("enabled", False):
        return {
            "ok": False,
            "text": "Remote element healing service is unavailable.",
            "attachments": attachments,
            "data": {"fields": result_fields},
        }

    payload = _tool_payload(result_fields)
    if not payload:
        return {
            "ok": False,
            "text": "Device result is missing.",
            "attachments": attachments,
            "data": {"fields": result_fields},
        }
    target = _tool_target(result_fields, payload)

    async def collect_heal_locator() -> tuple[
        dict[str, typing.Any] | None,
        dict[str, typing.Any],
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
                    if reporter is not None:
                        await reporter.display(message)
                    else:
                        observe("enhance.heal.step", message_chars=len(message))
                continue

            if event_type == "heal.failed":
                error = str(heal_event.get("error") or "unknown heal error")
                if reporter is not None:
                    await reporter.display(error)
                else:
                    observe(
                        "enhance.heal.failed",
                        level="WARNING",
                        error=error,
                    )
                result_data.update(
                    {
                        "target": target,
                        "error": heal_event.get("error"),
                    }
                )
                continue

            if event_type != "heal.result":
                continue
            heal_result = heal_event.get("result")
            if not isinstance(heal_result, dict):
                continue
            details = heal_result.get("details")
            selector_envelope = heal_result.get("new_selector")
            reason = (
                details.get("reason", "unknown")
                if isinstance(details, dict)
                else "unknown"
            )
            selector = (
                selector_envelope.get("primary")
                if isinstance(selector_envelope, dict)
                else None
            )
            selector_fields = selector if isinstance(selector, dict) else {}
            heal_locator = {
                "by": selector_fields.get("by"),
                "value": selector_fields.get("value"),
            }
            result_data.update(
                {
                    "target": target,
                    "locator": heal_locator,
                    "reason": reason,
                }
            )
            if reporter is not None:
                await reporter.display(str(reason))
            return heal_locator, result_data

        return None, result_data

    if reporter is not None:
        await reporter.begin_status()
    try:
        locator, heal_result_data = await collect_heal_locator()
    finally:
        if reporter is not None:
            await reporter.end_status()

    if not locator:
        return {
            "ok": False,
            "text": "Element location failed.",
            "attachments": attachments,
            "data": heal_result_data,
        }
    return {
        "ok": True,
        "text": "Element location succeeded.",
        "attachments": attachments,
        "data": heal_result_data,
    }


if __name__ == '__main__':
    pass
