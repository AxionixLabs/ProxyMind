# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import hashlib
import json
import os
import typing
from pathlib import Path

__all__ = (
    "PermissionGrantScope",
    "PermissionProfile",
    "PermissionValue",
    "copy_permission_profile",
    "intersect_permission_profiles",
    "merge_permission_profiles",
    "normalize_permission_profile",
    "normalize_working_directory",
    "permission_profile_covers",
    "permission_profile_key",
)


PermissionScalar: typing.TypeAlias = None | bool | int | float | str
PermissionValue: typing.TypeAlias = (
    PermissionScalar
    | list["PermissionValue"]
    | dict[str, "PermissionValue"]
)
PermissionProfile: typing.TypeAlias = dict[str, PermissionValue]
PermissionGrantScope: typing.TypeAlias = typing.Literal["turn", "session"]


def copy_permission_profile(value: object) -> PermissionProfile:
    """复制权限对象；非对象值按空权限处理。"""
    if not isinstance(value, dict):
        return {}
    return _copy_object(value, field_name="permissions")


def normalize_permission_profile(
    value: object,
    *,
    cwd: str | os.PathLike[str] | None,
) -> PermissionProfile:
    """校验并规范化权限申请及其中的工作区相对路径。"""
    if not isinstance(value, dict):
        raise ValueError("permissions must be an object")

    unknown = set(value).difference({"network", "file_system"})
    if unknown:
        raise ValueError(
            "permissions contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
        )

    result: PermissionProfile = {}
    network = value.get("network")
    if network is not None:
        if not isinstance(network, dict):
            raise ValueError("permissions.network must be an object")
        unknown_network = set(network).difference({"enabled"})
        if unknown_network:
            raise ValueError(
                "permissions.network contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown_network))
            )
        enabled = network.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("permissions.network.enabled must be boolean")
        if enabled is not None:
            result["network"] = {"enabled": enabled}

    file_system = value.get("file_system")
    if file_system is not None:
        if not isinstance(file_system, dict):
            raise ValueError("permissions.file_system must be an object")
        unknown_file_system = set(file_system).difference({
            "read",
            "write",
            "entries",
            "glob_scan_max_depth",
        })
        if unknown_file_system:
            raise ValueError(
                "permissions.file_system contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown_file_system))
            )

        normalized_file_system: dict[str, PermissionValue] = {}
        for access in ("read", "write"):
            paths = file_system.get(access)
            if paths is None:
                continue
            if not isinstance(paths, list):
                raise ValueError(f"permissions.file_system.{access} must be a list")
            normalized_paths: list[PermissionValue] = []
            for path in paths:
                if not isinstance(path, str) or not path.strip():
                    raise ValueError(
                        f"permissions.file_system.{access} must contain non-empty strings"
                    )
                normalized_path = _normalize_permission_path(path, cwd)
                if normalized_path not in normalized_paths:
                    normalized_paths.append(normalized_path)
            if normalized_paths:
                normalized_file_system[access] = normalized_paths

        entries = file_system.get("entries")
        if entries is not None:
            if not isinstance(entries, list):
                raise ValueError("permissions.file_system.entries must be a list")
            normalized_entries: list[PermissionValue] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(
                        "permissions.file_system.entries must contain objects"
                    )
                unsupported = set(entry).difference({
                    "path",
                    "access",
                    "missing_path_behavior",
                })
                if unsupported:
                    raise ValueError(
                        "permissions.file_system entry contains unsupported fields: "
                        + ", ".join(sorted(str(item) for item in unsupported))
                    )
                path = entry.get("path")
                access = str(entry.get("access") or "").strip().casefold()
                if access not in {"read", "write", "deny"}:
                    raise ValueError(
                        "permissions.file_system entry access must be read, write or deny"
                    )
                normalized_entry = _copy_object(
                    entry,
                    field_name="permissions.file_system entry",
                )
                normalized_entry["path"] = _normalize_permission_path(path, cwd)
                normalized_entry["access"] = access
                if normalized_entry not in normalized_entries:
                    normalized_entries.append(normalized_entry)
            if normalized_entries:
                normalized_file_system["entries"] = normalized_entries

        depth = file_system.get("glob_scan_max_depth")
        if depth is not None:
            if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
                raise ValueError(
                    "permissions.file_system.glob_scan_max_depth must be a positive integer"
                )
            normalized_file_system["glob_scan_max_depth"] = depth

        if normalized_file_system:
            result["file_system"] = normalized_file_system

    if not result:
        raise ValueError("permissions must contain at least one permission")
    return result


def normalize_working_directory(
    value: str | os.PathLike[str] | None,
) -> str:
    """将工作目录规范化为权限身份使用的稳定路径。"""
    raw = str(value or ".").strip()
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return raw


def intersect_permission_profiles(
    requested: object,
    granted: object,
) -> PermissionProfile:
    """返回授权资料与原始申请的结构化交集。"""
    intersection = _intersect_value(
        copy_permission_profile(requested),
        copy_permission_profile(granted),
    )
    return intersection if isinstance(intersection, dict) else {}


def merge_permission_profiles(
    *profiles: PermissionProfile,
) -> PermissionProfile:
    """合并权限对象，并对列表中的 JSON 值去重。"""
    result: PermissionProfile = {}
    for profile in profiles:
        for key, value in profile.items():
            if key not in result:
                result[key] = copy.deepcopy(value)
                continue
            current = result[key]
            if isinstance(current, dict) and isinstance(value, dict):
                result[key] = merge_permission_profiles(current, value)
            elif isinstance(current, list) and isinstance(value, list):
                current.extend(
                    copy.deepcopy(item)
                    for item in value
                    if item not in current
                )
    return result


def permission_profile_covers(container: object, candidate: object) -> bool:
    """判断一个权限对象是否完整覆盖另一个权限对象。"""
    if isinstance(candidate, dict):
        return isinstance(container, dict) and all(
            key in container and permission_profile_covers(container[key], value)
            for key, value in candidate.items()
        )
    if isinstance(candidate, list):
        return isinstance(container, list) and all(
            any(permission_profile_covers(item, value) for item in container)
            for value in candidate
        )
    return container == candidate


def permission_profile_key(profile: PermissionProfile) -> str:
    """生成规范权限对象的稳定内容键。"""
    encoded = json.dumps(
        profile,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(memoryview(encoded.encode("utf-8"))).hexdigest()


def _normalize_permission_path(
    value: object,
    cwd: str | os.PathLike[str] | None,
) -> PermissionValue:
    """将权限路径按当前工作目录转换为稳定表示。"""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("permission path must be non-empty")
        if any(token in raw for token in ("*", "?", "[")):
            return raw
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(normalize_working_directory(cwd)) / path
        try:
            return str(path.resolve())
        except (OSError, RuntimeError, ValueError):
            return str(path)
    if isinstance(value, dict):
        path_value = value.get("path") or value.get("pattern")
        path_kind = str(value.get("type") or value.get("kind") or "path").strip()
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("structured permission path must contain path or pattern")
        normalized = _copy_object(value, field_name="structured permission path")
        key = "pattern" if path_kind in {"glob_pattern", "glob"} else "path"
        normalized[key] = _normalize_permission_path(path_value, cwd)
        return normalized
    raise ValueError("permission path must be a string or object")


def _intersect_value(
    requested: PermissionValue,
    granted: PermissionValue,
) -> PermissionValue:
    if isinstance(requested, dict) and isinstance(granted, dict):
        result: dict[str, PermissionValue] = {}
        for key, value in granted.items():
            if key in requested:
                item = _intersect_value(requested[key], value)
                if item not in (None, {}, []):
                    result[key] = item
        return result
    if isinstance(requested, list) and isinstance(granted, list):
        result: list[PermissionValue] = []
        for item in granted:
            for candidate in requested:
                intersection = _intersect_value(candidate, item)
                if intersection not in (None, {}, []):
                    result.append(intersection)
                    break
        return result
    return copy.deepcopy(granted) if requested == granted else None


def _copy_object(value: dict[object, object], *, field_name: str) -> PermissionProfile:
    result: PermissionProfile = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} contains a non-string field")
        result[key] = _copy_value(item, field_name=f"{field_name}.{key}")
    return result


def _copy_value(value: object, *, field_name: str) -> PermissionValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [
            _copy_value(item, field_name=field_name)
            for item in value
        ]
    if isinstance(value, dict):
        return _copy_object(value, field_name=field_name)
    raise ValueError(f"{field_name} contains an unsupported value")


if __name__ == '__main__':
    pass
