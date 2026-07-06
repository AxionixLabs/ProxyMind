# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

EXECUTION_ALLOWED    = {"allowed", "approved"}
POLICY_MANAGED_TOOLS = {"shell_command", "shell_calls", "exec_command"}


def validate_execution_policy(
    *,
    name: str,
    arguments: dict[str, typing.Any],
    execution: dict[str, typing.Any] | None
) -> dict[str, typing.Any] | None:
    """在分发本地工具调用前校验执行元数据。"""
    if name not in POLICY_MANAGED_TOOLS:
        return None

    if not isinstance(execution, dict) or not execution:
        return _reject("missing execution")

    state  = str(execution.get("state") or "").strip().lower()
    target = str(execution.get("target") or "").strip().lower()

    if name == "shell_calls" and not isinstance(arguments.get("items"), list):
        return _reject("shell_calls items missing")
    if name == "shell_command" and not str(arguments.get("command") or "").strip():
        return _reject("shell_command command missing")
    if name == "exec_command" and not str(arguments.get("command") or "").strip():
        return _reject("exec_command command missing")

    canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")
    if (
        isinstance(canonical, dict)
        and _canonical_arguments(name, arguments) != _canonical_arguments(name, canonical)
    ):
        return _reject("execution canonical arguments mismatch")

    grant_id = execution.get("grantId") or execution.get("grant_id")
    if not str(grant_id or "").strip():
        return _reject("execution grantId missing")

    if target != "local":
        return _ignore("server-owned execution target")

    if state and state not in EXECUTION_ALLOWED:
        return _reject(f"execution state not executable: {state}")

    return None


def should_pass_execution_to_tool(name: str, execution: dict[str, typing.Any] | None) -> bool:
    """判断是否需要把执行授权元数据传给本地工具。"""
    if not isinstance(execution, dict) or name not in POLICY_MANAGED_TOOLS:
        return False
    target = str(execution.get("target") or "local").strip().lower()
    return target == "local"


def is_execution_ignored(result: dict[str, typing.Any] | None) -> bool:
    """判断策略结果是否表示客户端忽略本次工具调用。"""
    return isinstance(result, dict) and result.get("execution_ignored") is True


def _reject(message: str) -> dict[str, typing.Any]:
    """构造执行策略拒绝结果。"""
    return {"execution_denied": True, "error": message}


def _ignore(message: str) -> dict[str, typing.Any]:
    """构造执行策略忽略结果。"""
    return {"execution_ignored": True, "reason": message}


def _normalize_value(value: typing.Any) -> typing.Any:
    """将策略比较值转换为稳定的基础结构。"""
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
    if name not in POLICY_MANAGED_TOOLS or not isinstance(value, dict):
        return _normalize_value(value)

    if name == "shell_calls":
        return {
            "items": [_canonical_shell_item(item) for item in value.get("items") or []]
        }
    if name == "exec_command":
        return _canonical_exec_command_item(value)

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


def _canonical_exec_command_item(
    value: typing.Any
) -> dict[str, typing.Any]:
    """归一化 exec_command 参数。"""
    item = value if isinstance(value, dict) else {}

    return {
        "command"          : str(item.get("command") or ""),
        "cwd"              : str(item.get("cwd") or "."),
        "yield_time_ms"    : _int_default(item.get("yield_time_ms"), 1000),
        "max_output_chars" : int(item.get("max_output_chars") or 24000),
        "timeout_sec"      : int(item.get("timeout_sec") or 1800),
        "idle_timeout_sec" : int(item.get("idle_timeout_sec") or 300)
    }


def _int_default(value: typing.Any, default: int) -> int:
    """转换整数并保留有效的 0 值。"""
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


if __name__ == '__main__':
    pass
