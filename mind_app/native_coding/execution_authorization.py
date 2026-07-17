# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import time
import typing
from datetime import (
    datetime, timezone
)

EXECUTION_ALLOWED_STATES = {"allowed", "approved"}

_TOOL_FIELDS: dict[str, set[str]] = {
    "shell_command": {"command", "cwd", "timeout_sec", "output_encoding"},
    "exec_command": {
        "command",
        "cwd",
        "yield_time_ms",
        "max_output_chars",
        "timeout_sec",
        "idle_timeout_sec",
    },
    "write_stdin": {
        "session_id",
        "stdin",
        "wait_ms",
        "max_output_chars",
        "control",
    },
}

_WRITE_CONTROLS = {"none", "interrupt", "eof", "terminate", "kill"}


class ExecutionAuthorizationError(ValueError):
    """描述执行授权或 canonical 契约校验失败。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def canonical_arguments(
    execution: dict[str, typing.Any] | None,
    *,
    tool: str,
) -> dict[str, typing.Any]:
    """读取并严格校验指定工具的 canonical 参数。"""
    if not isinstance(execution, dict) or not execution:
        raise ExecutionAuthorizationError(
            "execution_metadata_required", "execution metadata is required"
        )

    canonical = execution.get("canonicalArguments")
    if not isinstance(canonical, dict):
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid", "canonicalArguments must be an object"
        )

    expected_fields = _TOOL_FIELDS.get(tool)
    if expected_fields is None:
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid", f"unsupported canonical tool: {tool}"
        )
    _require_exact_fields(canonical, expected_fields, path="canonicalArguments")

    if tool == "shell_command":
        _validate_shell_item(canonical, path="canonicalArguments", timeout_max=600)
    elif tool == "exec_command":
        _validate_exec_command(canonical)
    elif tool == "write_stdin":
        _validate_write_stdin(canonical)

    return canonical


def validate_execution_authorization(
    execution: dict[str, typing.Any] | None,
    *,
    require_grant: bool = True,
) -> str | None:
    """校验执行状态、目标、版本、有效期和 grant。"""
    if not isinstance(execution, dict) or not execution:
        raise ExecutionAuthorizationError(
            "execution_metadata_required", "execution metadata is required"
        )

    state = execution.get("state")
    if not isinstance(state, str) or state.strip().lower() not in EXECUTION_ALLOWED_STATES:
        raise ExecutionAuthorizationError(
            "execution_state_not_executable", "execution state is not executable"
        )

    target = execution.get("target")
    if not isinstance(target, str) or target.strip().lower() != "local":
        raise ExecutionAuthorizationError(
            "execution_target_invalid", "execution target must be local"
        )

    policy_version = execution.get("policyVersion")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise ExecutionAuthorizationError(
            "execution_policy_version_missing", "policyVersion is required"
        )

    expires_at = execution_expiration_timestamp(execution)
    if expires_at <= time.time():
        raise ExecutionAuthorizationError(
            "execution_authorization_expired", "execution authorization has expired"
        )

    grant_id = execution.get("grantId")
    if require_grant and (not isinstance(grant_id, str) or not grant_id.strip()):
        raise ExecutionAuthorizationError(
            "execution_grant_id_missing", "grantId is required"
        )
    return grant_id.strip() if isinstance(grant_id, str) and grant_id.strip() else None


def execution_expiration_timestamp(
    execution: dict[str, typing.Any] | None,
) -> float:
    """读取执行授权的 Unix 过期时间。"""
    if not isinstance(execution, dict) or not execution:
        raise ExecutionAuthorizationError(
            "execution_metadata_required", "execution metadata is required"
        )
    return _expiration_timestamp(execution.get("expiresAt"))


def validate_runtime_identity(*, cid: typing.Any, sid: typing.Any, call_id: typing.Any) -> None:
    """校验可信工具事件携带的调用身份。"""
    missing = [
        name
        for name, value in (("cid", cid), ("sid", sid), ("call_id", call_id))
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ExecutionAuthorizationError(
            "execution_identity_missing", f"missing runtime identity: {', '.join(missing)}"
        )


def _validate_shell_item(
    value: dict[str, typing.Any],
    *,
    path: str,
    timeout_max: int,
) -> None:
    """校验单条 shell canonical 参数。"""
    _require_nonempty_string(value["command"], path=f"{path}.command")
    _require_nonempty_string(value["cwd"], path=f"{path}.cwd")
    _require_int(
        value["timeout_sec"],
        path=f"{path}.timeout_sec",
        minimum=1,
        maximum=timeout_max,
    )
    _require_nonempty_string(value["output_encoding"], path=f"{path}.output_encoding")


def _validate_exec_command(canonical: dict[str, typing.Any]) -> None:
    """校验持续命令 canonical 参数。"""
    _require_nonempty_string(canonical["command"], path="canonicalArguments.command")
    _require_nonempty_string(canonical["cwd"], path="canonicalArguments.cwd")
    _require_int(
        canonical["yield_time_ms"],
        path="canonicalArguments.yield_time_ms",
        minimum=0,
        maximum=30000,
    )
    _require_int(
        canonical["max_output_chars"],
        path="canonicalArguments.max_output_chars",
        minimum=1024,
        maximum=120000,
    )
    _require_int(
        canonical["timeout_sec"],
        path="canonicalArguments.timeout_sec",
        minimum=1,
        maximum=7200,
    )
    _require_int(
        canonical["idle_timeout_sec"],
        path="canonicalArguments.idle_timeout_sec",
        minimum=1,
        maximum=1800,
    )


def _validate_write_stdin(canonical: dict[str, typing.Any]) -> None:
    """校验会话写入 canonical 参数。"""
    session_id = _require_nonempty_string(
        canonical["session_id"], path="canonicalArguments.session_id"
    )
    if session_id != session_id.strip():
        _invalid("canonicalArguments.session_id must not contain surrounding whitespace")

    _require_string(canonical["stdin"], path="canonicalArguments.stdin")

    _require_int(
        canonical["wait_ms"],
        path="canonicalArguments.wait_ms",
        minimum=0,
        maximum=30000,
    )
    _require_int(
        canonical["max_output_chars"],
        path="canonicalArguments.max_output_chars",
        minimum=1024,
        maximum=120000,
    )

    control = _require_nonempty_string(
        canonical["control"], path="canonicalArguments.control"
    )

    if control not in _WRITE_CONTROLS:
        _invalid("canonicalArguments.control is invalid")
    if canonical["stdin"] and control != "none":
        _invalid("canonicalArguments cannot combine non-empty stdin with control")


def _expiration_timestamp(value: typing.Any) -> float:
    """把授权过期时间转换为 Unix 时间戳。"""
    if isinstance(value, bool) or value is None:
        raise ExecutionAuthorizationError(
            "execution_expiration_invalid", "expiresAt is required"
        )

    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000.0 if timestamp >= 100_000_000_000 else timestamp

    if not isinstance(value, str) or not value.strip():
        raise ExecutionAuthorizationError(
            "execution_expiration_invalid", "expiresAt is invalid"
        )

    raw = value.strip()
    try:
        timestamp = float(raw)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExecutionAuthorizationError(
                "execution_expiration_invalid", "expiresAt is invalid"
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    return timestamp / 1000.0 if timestamp >= 100_000_000_000 else timestamp


def _require_exact_fields(
    value: dict[str, typing.Any],
    expected: set[str],
    *,
    path: str,
) -> None:
    """要求对象字段与契约完全一致。"""
    actual = set(value)
    if actual == expected:
        return

    missing = sorted(expected - actual)
    extra   = sorted(actual - expected)

    parts: list[str] = []

    if missing:
        parts.append(f"missing={missing}")
    if extra:
        parts.append(f"extra={extra}")
    _invalid(f"{path} fields invalid: {', '.join(parts)}")


def _require_string(value: typing.Any, *, path: str) -> str:
    """要求字段为字符串。"""
    if not isinstance(value, str):
        _invalid(f"{path} must be a string")
    return value


def _require_nonempty_string(value: typing.Any, *, path: str) -> str:
    """要求字段为非空字符串。"""
    text = _require_string(value, path=path)
    if not text.strip():
        _invalid(f"{path} must not be empty")
    return text


def _require_int(
    value: typing.Any,
    *,
    path: str,
    minimum: int,
    maximum: int,
) -> int:
    """要求字段为指定范围内的整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        _invalid(f"{path} must be between {minimum} and {maximum}")
    return value


def _invalid(detail: str) -> typing.NoReturn:
    """抛出 canonical 契约错误。"""
    raise ExecutionAuthorizationError("canonical_contract_invalid", detail)


if __name__ == '__main__':
    pass
