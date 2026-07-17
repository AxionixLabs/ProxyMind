# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

EXECUTION_ALLOWED    = {"allowed", "approved"}
POLICY_MANAGED_TOOLS = {"shell_command", "exec_command"}


def validate_execution_policy(
    *,
    name: str,
    execution: dict[str, typing.Any] | None
) -> dict[str, typing.Any] | None:
    """在分发本地工具调用前校验执行元数据。"""
    if name not in POLICY_MANAGED_TOOLS:
        return None

    if not isinstance(execution, dict) or not execution:
        return _reject("missing execution")

    state  = str(execution.get("state") or "").strip().lower()
    target = str(execution.get("target") or "").strip().lower()

    grant_id = execution.get("grantId") or execution.get("grant_id")
    if not str(grant_id or "").strip():
        return _reject("execution grantId missing")

    if target != "local":
        return _ignore("server-owned execution target")

    if state and state not in EXECUTION_ALLOWED:
        return _reject(f"execution state not executable: {state}")

    return None


def is_execution_ignored(result: dict[str, typing.Any] | None) -> bool:
    """判断策略结果是否表示客户端忽略本次工具调用。"""
    return isinstance(result, dict) and result.get("execution_ignored") is True


def _reject(message: str) -> dict[str, typing.Any]:
    """构造执行策略拒绝结果。"""
    return {"execution_denied": True, "error": message}


def _ignore(message: str) -> dict[str, typing.Any]:
    """构造执行策略忽略结果。"""
    return {"execution_ignored": True, "reason": message}


if __name__ == '__main__':
    pass
