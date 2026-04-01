# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from engine.tinker import MindError


@dataclass(slots=True)
class CodeSourceResolved:
    """标准化后的星图执行源。"""
    kind: typing.Literal["file", "stdin", "inline", "url"]
    name: str
    content: str
    display_origin: str
    identity: str
    cache_hit: bool = False
    fetched_at_ms: int | None = None


@dataclass(slots=True)
class CodeSourceAuth:
    """URL 星图拉取时使用的认证模型。"""
    type: typing.Literal["bearer", "basic"]
    token: str | None = None
    username: str | None = None
    password: str | None = None

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "type"     : self.type,
            "token"    : self.token,
            "username" : self.username,
            "password" : self.password,
        }

    @classmethod
    def from_input(cls, value: typing.Any) -> "CodeSourceAuth":
        if not isinstance(value, dict):
            raise MindError("code source auth must be an object")

        allowed = {"type", "token", "username", "password"}
        extra = set(value) - allowed
        if extra:
            raise MindError(f"Unsupported code source auth field(s): {', '.join(sorted(extra))}")

        auth_type = str(value.get("type") or "").strip().lower()
        if auth_type not in {"bearer", "basic"}:
            raise MindError("code source auth.type must be bearer or basic")

        auth = cls(
            type=typing.cast(typing.Literal["bearer", "basic"], auth_type),
            token=None if value.get("token") is None else str(value.get("token")),
            username=None if value.get("username") is None else str(value.get("username")),
            password=None if value.get("password") is None else str(value.get("password")),
        )

        if auth.type == "bearer":
            if not str(auth.token or "").strip():
                raise MindError("bearer auth requires token")
            return auth

        if not str(auth.username or "").strip():
            raise MindError("basic auth requires username")
        if auth.password is None:
            raise MindError("basic auth requires password")
        return auth


@dataclass(slots=True)
class CodeSourcePayload:
    """统一的星图来源 schema。"""
    kind: typing.Literal["file", "stdin", "inline", "url"]
    name: str | None = None
    path: str | None = None
    content: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    auth: CodeSourceAuth | None = None
    timeout_sec: float | None = None
    cache_ttl_sec: int | None = None
    max_content_bytes: int | None = None

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "kind"              : self.kind,
            "name"              : self.name,
            "path"              : self.path,
            "content"           : self.content,
            "url"               : self.url,
            "headers"           : self.headers,
            "auth"              : None if self.auth is None else self.auth.to_dict(),
            "timeout_sec"       : self.timeout_sec,
            "cache_ttl_sec"     : self.cache_ttl_sec,
            "max_content_bytes" : self.max_content_bytes,
        }

    @classmethod
    def from_input(cls, value: typing.Any) -> "CodeSourcePayload":
        if isinstance(value, cls):
            return value

        if not isinstance(value, dict):
            raise MindError("code source payload must be an object")

        allowed = {
            "kind", "name", "path", "content", "url", "headers",
            "auth", "timeout_sec", "cache_ttl_sec", "max_content_bytes",
        }
        extra = set(value) - allowed
        if extra:
            raise MindError(f"Unsupported code source field(s): {', '.join(sorted(extra))}")

        kind = str(value.get("kind") or "").strip().lower()
        if kind not in {"file", "stdin", "inline", "url"}:
            raise MindError("code source kind must be file, stdin, inline, or url")

        headers_raw = value.get("headers")
        if headers_raw is not None and not isinstance(headers_raw, dict):
            raise MindError("code source headers must be an object")

        payload = cls(
            kind=typing.cast(typing.Literal["file", "stdin", "inline", "url"], kind),
            name=None if value.get("name") is None else str(value.get("name")),
            path=None if value.get("path") is None else str(value.get("path")),
            content=None if value.get("content") is None else str(value.get("content")),
            url=None if value.get("url") is None else str(value.get("url")),
            headers=None if headers_raw is None else {str(k): str(v) for k, v in headers_raw.items()},
            auth=None if value.get("auth") is None else CodeSourceAuth.from_input(value.get("auth")),
            timeout_sec=None if value.get("timeout_sec") in (None, "") else float(value.get("timeout_sec")),
            cache_ttl_sec=None if value.get("cache_ttl_sec") in (None, "") else int(value.get("cache_ttl_sec")),
            max_content_bytes=None if value.get("max_content_bytes") in (None, "") else int(value.get("max_content_bytes")),
        )

        if payload.kind == "file":
            if not str(payload.path or "").strip():
                raise MindError("file source requires path")
            return payload

        if payload.kind == "stdin":
            return payload

        if payload.kind == "inline":
            if not str(payload.content or "").strip():
                raise MindError("inline source requires content")
            return payload

        if not str(payload.url or "").strip():
            raise MindError("url source requires url")
        return payload


if __name__ == '__main__':
    pass
