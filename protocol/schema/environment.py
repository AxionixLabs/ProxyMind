# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import copy
import typing
import datetime

EnvironmentStatus = typing.Literal[
    "available",
    "starting",
    "unavailable",
]

_SNAPSHOT_ID_PATTERN = re.compile(r"^envsnap_[A-Za-z0-9_-]+$")


class EnvironmentCapability(typing.TypedDict):
    """描述客户端声明的单项工具能力。"""

    available: bool
    command: str | None
    executable: str | None
    path: str | None
    version: str | None
    source: str | None


class EnvironmentShell(typing.TypedDict):
    """描述客户端本地 shell 的调用语义。"""

    name: str
    syntax: str
    executable: str | None
    prefix: list[str]
    source: str | None


class EnvironmentWorkspace(typing.TypedDict):
    """描述客户端当前工作区及候选执行根目录。"""

    root: str
    allowed_roots: list[str]
    source: str


class EnvironmentProvider(typing.TypedDict):
    """描述客户端聚合的单个工具能力提供方。"""

    tools: dict[str, EnvironmentCapability]
    extensions: dict[str, typing.Any]


class ClientEnvironmentSnapshot(typing.TypedDict):
    """表示一个 Turn 全生命周期使用的不可变环境快照。"""

    snapshot_id: str
    source: typing.Literal["client"]
    captured_at: str
    environment_id: str
    cwd: str
    status: EnvironmentStatus
    status_detail: str | None
    shell: EnvironmentShell
    workspace: EnvironmentWorkspace
    tools: dict[str, EnvironmentCapability]
    providers: dict[str, EnvironmentProvider]
    extensions: dict[str, typing.Any]


def normalize_client_environment_snapshot(
    value: typing.Any,
) -> ClientEnvironmentSnapshot:
    """校验并复制客户端环境快照，拒绝未声明字段。"""
    data = _mapping(value, label="exec_env")
    required = frozenset({
        "snapshot_id",
        "source",
        "captured_at",
        "environment_id",
        "cwd",
        "status",
        "shell",
        "workspace",
    })
    allowed = required.union({
        "status_detail",
        "tools",
        "providers",
        "extensions",
    })
    _require_fields(
        data,
        allowed=allowed,
        required=required,
        label="exec_env",
    )

    snapshot_id = _required_text(data.get("snapshot_id"), label="snapshot_id")
    if not _SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ValueError("exec_env snapshot_id must use the envsnap_ prefix")
    if data.get("source") != "client":
        raise ValueError("exec_env source must be client")

    return ClientEnvironmentSnapshot(
        snapshot_id=snapshot_id,
        source="client",
        captured_at=_captured_at(data.get("captured_at")),
        environment_id=_required_text(
            data.get("environment_id"),
            label="environment_id",
        ),
        cwd=_required_text(data.get("cwd"), label="cwd"),
        status=_environment_status(data.get("status")),
        status_detail=_optional_text(
            data.get("status_detail"),
            label="status_detail",
        ),
        shell=_shell(data.get("shell")),
        workspace=_workspace(data.get("workspace")),
        tools=_capabilities(data.get("tools", {}), label="tools"),
        providers=_providers(data.get("providers", {})),
        extensions=_extensions(data.get("extensions", {}), label="extensions"),
    )


def normalize_environment_provider(value: typing.Any) -> EnvironmentProvider:
    """校验并复制单个客户端工具能力提供方。"""
    data = _mapping(value, label="provider")
    fields = frozenset({"tools", "extensions"})
    _require_fields(
        data,
        allowed=fields,
        required=frozenset(),
        label="provider",
    )
    return EnvironmentProvider(
        tools=_capabilities(data.get("tools", {}), label="provider.tools"),
        extensions=_extensions(
            data.get("extensions", {}),
            label="provider.extensions",
        ),
    )


def _shell(value: typing.Any) -> EnvironmentShell:
    data = _mapping(value, label="shell")
    _require_fields(
        data,
        allowed=frozenset({"name", "syntax", "executable", "prefix", "source"}),
        required=frozenset({"name", "syntax"}),
        label="shell",
    )
    return EnvironmentShell(
        name=_required_text(data.get("name"), label="shell.name"),
        syntax=_required_text(data.get("syntax"), label="shell.syntax"),
        executable=_optional_text(
            data.get("executable"),
            label="shell.executable",
        ),
        prefix=_text_list(data.get("prefix", []), label="shell.prefix"),
        source=_optional_text(data.get("source"), label="shell.source"),
    )


def _workspace(value: typing.Any) -> EnvironmentWorkspace:
    data = _mapping(value, label="workspace")
    _require_fields(
        data,
        allowed=frozenset({"root", "allowed_roots", "source"}),
        required=frozenset({"root", "source"}),
        label="workspace",
    )
    return EnvironmentWorkspace(
        root=_required_text(data.get("root"), label="workspace.root"),
        allowed_roots=_unique_text_list(
            data.get("allowed_roots", []),
            label="workspace.allowed_roots",
        ),
        source=_required_text(data.get("source"), label="workspace.source"),
    )


def _capabilities(
    value: typing.Any,
    *,
    label: str,
) -> dict[str, EnvironmentCapability]:
    data = _mapping(value, label=label)
    capabilities: dict[str, EnvironmentCapability] = {}
    for name, capability in data.items():
        capability_name = _required_text(name, label=f"{label} name")
        capabilities[capability_name] = _capability(
            capability,
            label=f"{label}.{capability_name}",
        )
    return capabilities


def _capability(value: typing.Any, *, label: str) -> EnvironmentCapability:
    data = _mapping(value, label=label)
    _require_fields(
        data,
        allowed=frozenset({
            "available",
            "command",
            "executable",
            "path",
            "version",
            "source",
        }),
        required=frozenset({"available"}),
        label=label,
    )
    available = data.get("available")
    if not isinstance(available, bool):
        raise TypeError(f"{label}.available must be a boolean")
    return EnvironmentCapability(
        available=available,
        command=_optional_text(data.get("command"), label=f"{label}.command"),
        executable=_optional_text(
            data.get("executable"),
            label=f"{label}.executable",
        ),
        path=_optional_text(data.get("path"), label=f"{label}.path"),
        version=_optional_text(data.get("version"), label=f"{label}.version"),
        source=_optional_text(data.get("source"), label=f"{label}.source"),
    )


def _providers(value: typing.Any) -> dict[str, EnvironmentProvider]:
    data = _mapping(value, label="providers")
    providers: dict[str, EnvironmentProvider] = {}
    for name, provider in data.items():
        provider_name = _required_text(name, label="provider name")
        providers[provider_name] = normalize_environment_provider(provider)
    return providers


def _extensions(value: typing.Any, *, label: str) -> dict[str, typing.Any]:
    data = _mapping(value, label=label)
    _validate_extension_value(data, label=label, depth=0)
    return copy.deepcopy(data)


def _validate_extension_value(
    value: typing.Any,
    *,
    label: str,
    depth: int,
) -> None:
    if depth > 8:
        raise ValueError(f"{label} exceeds the supported nesting depth")
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_extension_value(item, label=label, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"{label} keys must be non-empty strings")
            _validate_extension_value(item, label=label, depth=depth + 1)
        return
    raise TypeError(f"{label} must contain JSON values")


def _mapping(value: typing.Any, *, label: str) -> dict[str, typing.Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{label} keys must be strings")
    return value


def _require_fields(
    data: dict[str, typing.Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(data).difference(allowed))
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")
    missing = sorted(required.difference(data))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def _captured_at(value: typing.Any) -> str:
    captured_at = _required_text(value, label="captured_at")
    parsed_text = (
        captured_at[:-1] + "+00:00"
        if captured_at.endswith("Z")
        else captured_at
    )
    try:
        parsed = datetime.datetime.fromisoformat(parsed_text)
    except ValueError as error:
        raise ValueError(
            "exec_env captured_at must be an ISO 8601 date-time"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("exec_env captured_at must include a timezone")
    return captured_at


def _environment_status(value: typing.Any) -> EnvironmentStatus:
    if value == "available":
        return "available"
    if value == "starting":
        return "starting"
    if value == "unavailable":
        return "unavailable"
    raise ValueError("exec_env status is invalid")


def _required_text(value: typing.Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: typing.Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label=label)


def _text_list(value: typing.Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a list")
    return [_required_text(item, label=label) for item in value]


def _unique_text_list(value: typing.Any, *, label: str) -> list[str]:
    items = _text_list(value, label=label)
    if len(set(items)) != len(items):
        raise ValueError(f"{label} must not contain duplicates")
    return items


if __name__ == '__main__':
    pass
