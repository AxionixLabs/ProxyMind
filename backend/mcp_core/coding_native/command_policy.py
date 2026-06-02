# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.
"""原生编码命令的执行元数据校验辅助。"""

import typing
from backend.mcp_core.coding_native.base import NativeCodingComponent


class CommandPolicy(NativeCodingComponent):
    """根据执行元数据生成命令运行策略。"""

    LONG_TASK_TIMEOUT_SEC  = 300
    DEFAULT_OUTPUT_LIMIT   = 24000
    LONG_TASK_OUTPUT_LIMIT = 12000

    @staticmethod
    def execution_metadata_policy(
        execution: dict[str, typing.Any] | None,
        *,
        command: list[str] | None = None,
        cwd: str = ".",
        timeout_sec: int
    ) -> dict[str, typing.Any]:
        """校验执行元数据，并返回统一的执行策略结果。"""
        if not isinstance(execution, dict) or not execution:
            return CommandPolicy._deny(
                "execution_metadata_required",
                risk="blocked",
                category="execution",
                reasons=["missing_execution_metadata"],
                execution_target="blocked"
            )

        grant_id = execution.get("grantId") or execution.get("grant_id")
        if not str(grant_id or "").strip():
            return CommandPolicy._deny(
                "execution_grant_id_missing",
                risk=execution.get("risk") or "blocked",
                category=execution.get("category") or "execution",
                reasons=["missing_grant_id"],
                execution_target="blocked",
                execution=execution
            )

        state   = str(execution.get("state") or "approved").strip().lower()
        target  = str(execution.get("target") or "local").strip().lower()
        timeout = max(1, int(timeout_sec or 60))

        if state not in {"allowed", "approved"}:
            return CommandPolicy._deny(
                "execution_state_not_executable",
                risk=execution.get("risk") or "blocked",
                category=execution.get("category") or "execution",
                reasons=[f"state:{state or 'missing'}"],
                execution_target="blocked",
                execution=execution
            )
        if target == "blocked":
            return CommandPolicy._deny(
                "execution_blocked",
                risk=execution.get("risk") or "blocked",
                category=execution.get("category") or "execution",
                reasons=list(execution.get("reasons") or ["blocked"]),
                execution_target="blocked",
                execution=execution
            )
        if target not in {"local", "cloud_sandbox"}:
            return CommandPolicy._deny(
                "execution_target_invalid",
                risk=execution.get("risk") or "blocked",
                category=execution.get("category") or "execution",
                reasons=[f"target:{target or 'missing'}"],
                execution_target="blocked",
                execution=execution
            )

        canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")
        if isinstance(canonical, dict):
            expected = {
                "command": list(command or []),
                "cwd": str(cwd or "."),
                "timeout_sec": int(timeout_sec or 60)
            }
            actual = {
                "command": canonical.get("command"),
                "cwd": canonical.get("cwd"),
                "timeout_sec": canonical.get("timeout_sec")
            }
            if CommandPolicy._normalize_policy_value(expected) != CommandPolicy._normalize_policy_value(actual):
                return CommandPolicy._deny(
                    "execution_canonical_arguments_mismatch",
                    risk=execution.get("risk") or "blocked",
                    category=execution.get("category") or "execution",
                    reasons=["canonical_arguments_mismatch"],
                    execution_target="blocked",
                    execution=execution
                )

        return CommandPolicy._allow(
            risk=execution.get("risk") or "execution",
            category=execution.get("category") or "execution",
            reasons=list(execution.get("reasons") or []),
            approval_required=False,
            execution_target=target,
            requires_cloud_sandbox=target == "cloud_sandbox",
            execution=execution,
            timeout_sec=timeout,
            output_limit=CommandPolicy.LONG_TASK_OUTPUT_LIMIT if timeout >= CommandPolicy.LONG_TASK_TIMEOUT_SEC else CommandPolicy.DEFAULT_OUTPUT_LIMIT,
            long_task=timeout >= CommandPolicy.LONG_TASK_TIMEOUT_SEC,
            grant_id=grant_id,
            approval_id=execution.get("approvalId") or execution.get("approval_id"),
            policy_version=execution.get("policyVersion") or execution.get("policy_version")
        )

    @staticmethod
    def _normalize_policy_value(
        value: typing.Any
    ) -> typing.Any:
        """把策略比较值归一化为稳定结构，便于参数一致性校验。"""
        if isinstance(value, dict):
            return {
                str(key): CommandPolicy._normalize_policy_value(value[key])
                for key in sorted(value, key=lambda item: str(item))
            }
        if isinstance(value, list):
            return [CommandPolicy._normalize_policy_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _allow(
        **data: typing.Any
    ) -> dict[str, typing.Any]:
        """构造允许执行的策略结果。"""
        payload: dict[str, typing.Any] = {"ok": True, **data}
        payload.setdefault("execution_target", "local")
        payload.setdefault("requires_cloud_sandbox", False)
        return payload

    @staticmethod
    def _deny(
        reason: str,
        *,
        risk: str,
        **data: typing.Any
    ) -> dict[str, typing.Any]:
        """构造拒绝执行的策略结果。"""
        payload: dict[str, typing.Any] = {
            "ok": False,
            "reason": reason,
            "risk": risk,
            **data
        }
        payload.setdefault("execution_target", "blocked")
        payload.setdefault("requires_cloud_sandbox", False)
        return payload


if __name__ == '__main__':
    pass
