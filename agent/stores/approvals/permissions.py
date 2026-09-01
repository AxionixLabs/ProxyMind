# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import threading
from dataclasses import dataclass
from pathlib import Path
from agent.domain.permission_profiles import (
    PermissionGrantScope,
    PermissionProfile,
    copy_permission_profile,
    intersect_permission_profiles,
    merge_permission_profiles,
    normalize_working_directory,
    permission_profile_covers,
    permission_profile_key,
)

__all__ = [
    "PermissionGrant",
    "PermissionGrantStore",
]


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """保存一次已批准权限及其作用范围。"""
    scope: PermissionGrantScope
    cid: str
    sid: str
    turn_id: str
    environment_id: str
    cwd: str
    permissions: PermissionProfile
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
        scope: PermissionGrantScope,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str | Path | None,
        permissions: PermissionProfile,
        requested_permissions: PermissionProfile | None = None,
        strict_auto_review: bool = False,
    ) -> None:
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
        granted = (
            intersect_permission_profiles(requested_permissions, permissions)
            if requested_permissions is not None
            else copy_permission_profile(permissions)
        )
        normalized_cwd = normalize_working_directory(cwd)

        grant_scope: PermissionGrantScope = (
            "turn" if normalized_scope == "turn" else "session"
        )
        grant = PermissionGrant(
            scope=grant_scope,
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
                    self._strict_turns.add(
                        (normalized_cid, normalized_sid, normalized_turn)
                    )
            else:
                key = (
                    normalized_sid,
                    grant.environment_id,
                    normalized_cwd,
                    permission_profile_key(grant.permissions),
                )
                previous = self._session_grants.get(key)
                self._session_grants[key] = _merge_grants(previous, grant)
        return None

    def has_grant(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str | Path | None,
        permissions: PermissionProfile,
    ) -> bool:
        """判断指定上下文是否已有覆盖申请的权限。"""
        requested = copy_permission_profile(permissions)
        if not requested:
            return False

        env            = str(environment_id or "").strip()
        normalized_cwd = normalize_working_directory(cwd)
        key_prefix = (
            str(cid or "").strip(),
            str(sid or "").strip(),
            str(turn_id or "").strip(),
        )

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
        merged = merge_permission_profiles(
            *(grant.permissions for grant in candidates)
        )
        return permission_profile_covers(merged, requested)

    def strict_auto_review_enabled(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> bool:
        """判断当前 Turn 是否启用了严格自动审查。"""
        with self._lock:
            key = (
                str(cid or "").strip(),
                str(sid or "").strip(),
                str(turn_id or "").strip(),
            )
            return key in self._strict_turns

    def clear_turn(self, *, cid: str, sid: str, turn_id: str) -> None:
        """清理指定 Turn 的临时授权。"""
        key_prefix = (
            str(cid or "").strip(),
            str(sid or "").strip(),
            str(turn_id or "").strip(),
        )
        with self._lock:
            for key in tuple(self._turn_grants):
                if key[:3] == key_prefix:
                    self._turn_grants.pop(key, None)
            self._strict_turns.discard(key_prefix)


def _merge_grants(
    previous: PermissionGrant | None,
    current: PermissionGrant,
) -> PermissionGrant:
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
        permissions=merge_permission_profiles(
            previous.permissions,
            current.permissions,
        ),
        strict_auto_review=previous.strict_auto_review or current.strict_auto_review,
    )


if __name__ == '__main__':
    pass
