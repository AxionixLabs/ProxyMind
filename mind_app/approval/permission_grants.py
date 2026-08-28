# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import json
import typing
import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from mind_nova import const

__all__ = [
    "PermissionGrant",
    "PermissionGrantStore"
]


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """保存一次已批准权限及其作用范围。"""
    scope: typing.Literal["turn", "session"]
    cid: str
    sid: str
    turn_id: str
    environment_id: str
    cwd: str
    permissions: dict[str, typing.Any]
    strict_auto_review: bool = False

    def __post_init__(self) -> None:
        """复制权限资料，避免调用方修改已记录的授权。"""
        object.__setattr__(self, "permissions", copy.deepcopy(self.permissions))


class PermissionGrantStore:
    """管理当前应用会话内的 Turn 和 session 权限授权。"""

    def __init__(self) -> None:
        """创建空的权限授权存储。"""
        self._turn_grants: dict[tuple[str, str, str, str, str], PermissionGrant] = {}
        self._session_grants: dict[tuple[str, str, str, str], PermissionGrant]   = {}

        self._strict_turns: set[tuple[str, str, str]] = set()

        self._lock = threading.RLock()

    @property
    def turn_grants(self) -> tuple[PermissionGrant, ...]:
        """返回当前存储的 Turn 授权快照。"""
        with self._lock:
            return tuple(copy.deepcopy(tuple(self._turn_grants.values())))

    @property
    def session_grants(self) -> tuple[PermissionGrant, ...]:
        """返回当前存储的 session 授权快照。"""
        with self._lock:
            return tuple(copy.deepcopy(tuple(self._session_grants.values())))

    @property
    def strict_auto_review(self) -> frozenset[tuple[str, str, str]]:
        """返回启用严格自动审查的 Turn 身份集合。"""
        with self._lock:
            return frozenset(self._strict_turns)

    def grant(
        self,
        *,
        scope: str,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str | Path | None,
        permissions: typing.Any,
        requested_permissions: typing.Any = None,
        strict_auto_review: bool = False,
    ) -> PermissionGrant:
        """记录一项受环境、目录和作用域限制的权限授权。"""
        normalized_scope = str(scope or "").strip().casefold()
        if normalized_scope not in {"turn", "session"}:
            raise ValueError("permission grant scope must be turn or session")
        if normalized_scope == "session" and strict_auto_review:
            raise ValueError("strict auto review is limited to turn grants")

        normalized_cid  = str(cid or "").strip()
        normalized_sid  = str(sid or "").strip()
        normalized_turn = str(turn_id or "").strip()

        if not normalized_sid or not normalized_turn:
            raise ValueError("permission grant requires sid and turn_id")
        if normalized_scope == "session" and not normalized_sid:
            raise ValueError("session permission grant requires sid")

        granted = _permission_intersection(
            requested_permissions,
            permissions,
        ) if requested_permissions is not None else _profile(permissions)
        normalized_cwd = _normalize_cwd(cwd)

        grant = PermissionGrant(
            scope=normalized_scope,
            cid=normalized_cid,
            sid=normalized_sid,
            turn_id=normalized_turn,
            environment_id=str(environment_id or "").strip(),
            cwd=normalized_cwd,
            permissions=granted,
            strict_auto_review=bool(strict_auto_review),
        )

        with self._lock:
            if normalized_scope == "turn":
                key = (
                    normalized_cid,
                    normalized_sid,
                    normalized_turn,
                    grant.environment_id,
                    normalized_cwd,
                )
                previous = self._turn_grants.get(key)
                self._turn_grants[key] = _merge_grants(previous, grant)
                if grant.strict_auto_review:
                    self._strict_turns.add((normalized_cid, normalized_sid, normalized_turn))
            else:
                key = (
                    normalized_sid,
                    grant.environment_id,
                    normalized_cwd,
                    _profile_key(grant.permissions),
                )
                previous = self._session_grants.get(key)
                self._session_grants[key] = _merge_grants(previous, grant)
        return grant

    def has_grant(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str | Path | None,
        permissions: typing.Any,
    ) -> bool:
        """判断指定上下文是否已有覆盖申请的权限。"""
        requested = _profile(permissions)
        if not requested:
            return False

        env            = str(environment_id or "").strip()
        normalized_cwd = _normalize_cwd(cwd)
        key_prefix     = (str(cid or "").strip(), str(sid or "").strip(), str(turn_id or "").strip())

        with self._lock:
            candidates = [
                grant
                for key, grant in self._turn_grants.items()
                if key[:3] == key_prefix
                and grant.environment_id == env
                and grant.cwd == normalized_cwd
            ]
            candidates.extend(
                grant
                for key, grant in self._session_grants.items()
                if key[:3] == (key_prefix[1], env, normalized_cwd)
            )
        merged = _merge_profiles(*(grant.permissions for grant in candidates))
        return _permission_covers(merged, requested)

    def strict_auto_review_enabled(self, *, cid: str, sid: str, turn_id: str) -> bool:
        """判断当前 Turn 是否启用了严格自动审查。"""
        with self._lock:
            return (str(cid or "").strip(), str(sid or "").strip(), str(turn_id or "").strip()) in self._strict_turns

    def clear_turn(self, *, cid: str, sid: str, turn_id: str) -> None:
        """清理指定 Turn 的临时授权。"""
        key_prefix = (str(cid or "").strip(), str(sid or "").strip(), str(turn_id or "").strip())
        with self._lock:
            for key in tuple(self._turn_grants):
                if key[:3] == key_prefix:
                    self._turn_grants.pop(key, None)
            self._strict_turns.discard(key_prefix)


def _profile(value: typing.Any) -> dict[str, typing.Any]:
    """复制并限制权限资料为对象。"""
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _normalize_cwd(value: str | Path | None) -> str:
    """将工作目录规范化为稳定路径。"""
    raw = str(value or ".").strip()
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return raw


def _merge_grants(previous: PermissionGrant | None, current: PermissionGrant) -> PermissionGrant:
    """合并同一作用域键下的权限资料。"""
    if previous is None:
        return current
    return PermissionGrant(
        scope=current.scope,
        cid=current.cid,
        sid=current.sid,
        turn_id=current.turn_id,
        environment_id=current.environment_id,
        cwd=current.cwd,
        permissions=_merge_profiles(previous.permissions, current.permissions),
        strict_auto_review=previous.strict_auto_review or current.strict_auto_review,
    )


def _merge_profiles(*profiles: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """合并权限对象并按 JSON 值去重列表。"""
    result: dict[str, typing.Any] = {}
    for profile in profiles:
        for key, value in profile.items():
            if key not in result:
                result[key] = copy.deepcopy(value)
            elif isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = _merge_profiles(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                result[key].extend(
                    copy.deepcopy(item)
                    for item in value
                    if not any(_json_equal(item, existing) for existing in result[key])
                )
    return result


def _permission_intersection(requested: typing.Any, granted: typing.Any) -> dict[str, typing.Any]:
    """取授权资料与申请资料的结构化交集。"""
    requested_profile = _profile(requested)
    granted_profile   = _profile(granted)

    return _intersect_value(requested_profile, granted_profile) or {}


def _intersect_value(requested: typing.Any, granted: typing.Any) -> typing.Any:
    if isinstance(requested, dict) and isinstance(granted, dict):
        result = {}
        for key, value in granted.items():
            if key in requested:
                item = _intersect_value(requested[key], value)
                if item not in (None, {}, []):
                    result[key] = item
        return result
    if isinstance(requested, list) and isinstance(granted, list):
        result: list[typing.Any] = []
        for item in granted:
            for candidate in requested:
                intersection = _intersect_value(candidate, item)
                if intersection not in (None, {}, []):
                    result.append(intersection)
                    break
        return result
    return copy.deepcopy(granted) if requested == granted else None


def _permission_covers(container: typing.Any, candidate: typing.Any) -> bool:
    """判断授权资料是否覆盖申请资料。"""
    if isinstance(candidate, dict):
        return isinstance(container, dict) and all(
            key in container and _permission_covers(container[key], value)
            for key, value in candidate.items()
        )
    if isinstance(candidate, list):
        return isinstance(container, list) and all(
            any(_permission_covers(item, value) for item in container)
            for value in candidate
        )
    return container == candidate


def _profile_key(profile: dict[str, typing.Any]) -> str:
    """生成权限资料的稳定键。"""
    encoded = json.dumps(profile, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode(const.CHARSET)).hexdigest()


def _json_equal(left: typing.Any, right: typing.Any) -> bool:
    """比较两个 JSON 值。"""
    return left == right


if __name__ == '__main__':
    pass
