# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SUPPORTED_CLOUD_EXECUTION_TOOLS = {"shell_command"}
EXECUTION_ALLOWED_STATES        = {"allowed", "approved"}
EXECUTION_TARGETS               = {"local", "cloud_sandbox", "blocked"}


def validate_execution_policy(
    *,
    name: str,
    arguments: dict[str, typing.Any],
    execution: dict[str, typing.Any] | None
) -> dict[str, typing.Any] | None:
    """在分发本地工具调用前校验执行元数据。"""
    if not isinstance(execution, dict) or not execution:
        return None

    state  = str(execution.get("state") or "").strip().lower()
    target = str(execution.get("target") or "").strip().lower()

    canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")
    if isinstance(canonical, dict) and _normalize_value(arguments) != _normalize_value(canonical):
        return _reject("execution canonical arguments mismatch")

    grant_id = execution.get("grantId") or execution.get("grant_id")
    if not str(grant_id or "").strip():
        return _reject("execution grantId missing")

    if state and state not in EXECUTION_ALLOWED_STATES:
        return _reject(f"execution state not executable: {state}")
    if target and target not in EXECUTION_TARGETS:
        return _reject(f"execution target invalid: {target}")
    if target == "blocked":
        return _reject("execution target blocked")
    if target == "cloud_sandbox" and name not in SUPPORTED_CLOUD_EXECUTION_TOOLS:
        return _reject(f"cloud sandbox execution unsupported for tool: {name}")

    return None


def should_pass_execution_to_tool(name: str, execution: dict[str, typing.Any] | None) -> bool:
    return bool(isinstance(execution, dict) and name in SUPPORTED_CLOUD_EXECUTION_TOOLS)


def _reject(message: str) -> dict[str, typing.Any]:
    return {"execution_denied": True, "error": message}


def _normalize_value(value: typing.Any) -> typing.Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


if __name__ == '__main__':
    pass
