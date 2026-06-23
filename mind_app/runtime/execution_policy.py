# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

EXECUTION_ALLOWED = {"allowed", "approved"}
EXECUTION_TARGETS = {"local", "blocked"}


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

    if name == "shell_command" and not isinstance(arguments.get("items"), list):
        return _reject("shell_command items missing")

    canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")
    if (
        isinstance(canonical, dict)
        and _canonical_arguments(name, arguments) != _canonical_arguments(name, canonical)
    ):
        return _reject("execution canonical arguments mismatch")

    grant_id = execution.get("grantId") or execution.get("grant_id")
    if not str(grant_id or "").strip():
        return _reject("execution grantId missing")

    if state and state not in EXECUTION_ALLOWED:
        return _reject(f"execution state not executable: {state}")
    if target and target not in EXECUTION_TARGETS:
        return _reject(f"execution target invalid: {target}")
    if target == "blocked":
        return _reject("execution target blocked")

    return None


def should_pass_execution_to_tool(name: str, execution: dict[str, typing.Any] | None) -> bool:
    if not isinstance(execution, dict) or name != "shell_command":
        return False
    target = str(execution.get("target") or "local").strip().lower()
    return target == "local"


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


def _canonical_arguments(
    name: str,
    value: dict[str, typing.Any]
) -> typing.Any:
    """按工具语义归一化执行裁决比较参数。"""
    if name != "shell_command" or not isinstance(value, dict):
        return _normalize_value(value)

    if isinstance(value.get("items"), list):
        return {
            "items": [_canonical_shell_item(item) for item in value.get("items") or []]
        }

    return _canonical_shell_item(value)


def _canonical_shell_item(
    value: typing.Any
) -> dict[str, typing.Any]:
    """归一化单条 shell command 参数。"""
    item = value if isinstance(value, dict) else {}
    return {
        "command"     : str(item.get("command") or ""),
        "cwd"         : str(item.get("cwd") or "."),
        "timeout_sec" : int(item.get("timeout_sec") or 60)
    }


if __name__ == '__main__':
    pass
