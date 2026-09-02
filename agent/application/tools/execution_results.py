# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Iterable,
    Mapping,
)

from agent.application.tools.results import (
    LocalToolResult,
    LocalToolSource,
)

__all__ = (
    "client_execution_failure",
    "client_execution_result",
)


def client_execution_result(
    *,
    tool: str,
    arguments: Mapping[str, typing.Any],
    result: Mapping[str, typing.Any],
    target: str,
) -> LocalToolResult:
    """校验本地执行结果信封并投影为稳定工具结果。"""
    data_value = result.get("data")
    data = dict(data_value) if isinstance(data_value, Mapping) else {}
    data["target"] = target

    attachments = _result_items(result.get("attachments"))
    logs = _result_items(result.get("logs"))
    ok = bool(result.get("ok"))
    text = str(result.get("text") or data or "")

    return LocalToolResult(
        tool=tool,
        source=LocalToolSource.CLIENT,
        ok=ok,
        text=f"tool={tool} target={target} ok={ok} {text}",
        args=dict(arguments),
        attachments=attachments,
        data=data,
        logs=logs,
    )


def client_execution_failure(
    *,
    tool: str,
    arguments: Mapping[str, typing.Any],
    target: str,
    reason: str,
    details: Mapping[str, typing.Any] | None = None,
) -> LocalToolResult:
    """构造未进入执行器时的稳定本地失败结果。"""
    data = {"reason": reason, **dict(details or {})}
    data["failure_context"] = {
        key: data[key]
        for key in ("tool", "error")
        if data.get(key) not in (None, "")
    }
    return client_execution_result(
        tool=tool,
        arguments=arguments,
        result={
            "ok": False,
            "text": f"local execution failed: {reason}",
            "attachments": (),
            "data": data,
            "logs": (),
        },
        target=target,
    )


def _result_items(value: typing.Any) -> tuple[typing.Any, ...]:
    """把结果数组收窄为不可变条目集合。"""
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise TypeError("local execution result items must be an array")
    if not isinstance(value, Iterable):
        raise TypeError("local execution result items must be an array")
    return tuple(value)


if __name__ == '__main__':
    pass
