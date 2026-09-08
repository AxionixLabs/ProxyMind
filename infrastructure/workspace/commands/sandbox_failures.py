# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

from infrastructure.platform.sandbox import SandboxError
from infrastructure.platform.sandbox import SandboxFailureStage


@dataclass(frozen=True, slots=True)
class SandboxToolFailure:
    """表示投影到本地工具结果的 Sandbox 失败事实。"""

    reason: str
    backend_code: str
    detail: str
    stage: SandboxFailureStage
    retryable: bool


def map_sandbox_failure(
    error: SandboxError,
    *,
    control: str | None = None,
) -> SandboxToolFailure:
    """把 Sandbox 边界失败唯一映射为本地工具失败事实。"""
    reason = error.code
    stage = error.stage
    if control == "interrupt":
        reason = "exec_interrupt_failed"
        stage = "control"
    elif control in {"terminate", "kill"}:
        reason = "exec_terminate_failed"
        stage = "control"
    elif control is not None:
        reason = "exec_stdin_closed"
        stage = "control"
    return SandboxToolFailure(
        reason=reason,
        backend_code=error.backend_code,
        detail=error.detail,
        stage=stage,
        retryable=error.retryable,
    )



if __name__ == '__main__':
    pass
