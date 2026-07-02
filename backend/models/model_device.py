# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from dataclasses import (
    dataclass, field
)
from backend.models.model_base import Attachment


@dataclass(slots=True)
class PrimitiveResult:
    ok: bool
    reason: str | None = None
    data: dict[str, typing.Any] | None = None

    @classmethod
    def success(cls, **data: typing.Any) -> "PrimitiveResult":
        return cls(
            ok=True,
            reason=None,
            data=data or None
        )

    @classmethod
    def fail(cls, reason: str, **data: typing.Any) -> "PrimitiveResult":
        return cls(
            ok=False,
            reason=reason,
            data=data or None
        )

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        return (self.data or {}).get(key, default)

    def to_dict(self) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"ok": self.ok}

        if self.reason is not None:
            payload["reason"] = self.reason

        if self.data:
            payload["data"] = self.data

        return payload


@dataclass(slots=True)
class ActionResult:
    ok: bool
    reason: str | None = None
    data: dict[str, typing.Any] | None = None

    @classmethod
    def success(cls, **data: typing.Any) -> "ActionResult":
        return cls(
            ok=True,
            reason=None,
            data=data or None
        )

    @classmethod
    def fail(cls, reason: str, **data: typing.Any) -> "ActionResult":
        return cls(
            ok=False,
            reason=reason,
            data=data or None
        )

    @classmethod
    def from_primitive(
        cls,
        primitive: PrimitiveResult,
        **extra: typing.Any,
    ) -> "ActionResult":
        merged = dict(primitive.data or {})
        merged.update(extra)
        return cls(
            ok=primitive.ok,
            reason=primitive.reason,
            data=merged or None
        )

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        return (self.data or {}).get(key, default)

    def to_dict(self) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"ok": self.ok}

        if self.reason is not None:
            payload["reason"] = self.reason

        if self.data:
            payload["data"] = self.data

        return payload


@dataclass(slots=True)
class SemanticResult:
    ok: bool = True
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    data: dict[str, typing.Any] | None = None
    logs: list[str] = field(default_factory=list)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        ok: bool = True,
        attachments: list[Attachment] | None = None,
        data: dict[str, typing.Any] | None = None,
        logs: list[str] | None = None,
    ) -> "SemanticResult":
        return cls(
            ok=ok,
            text=text,
            attachments=attachments or [],
            data=data,
            logs=logs or []
        )

    def add_log(self, message: str) -> None:
        if message:
            self.logs.append(message)

    def add_attachment(self, attachment: Attachment) -> None:
        self.attachments.append(attachment)

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "ok"          : self.ok,
            "text"        : self.text,
            "attachments" : [
                item.to_dict() if isinstance(item, Attachment) else item
                for item in self.attachments
            ],
            "data" : dict(self.data or {}),
            "logs" : list(self.logs)
        }


if __name__ == '__main__':
    pass
