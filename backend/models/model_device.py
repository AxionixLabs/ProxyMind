#  __  __           _      _   ____             _
# |  \/  | ___   __| | ___| | |  _ \  _____   _(_) ___ ___
# | |\/| |/ _ \ / _` |/ _ \ | | | | |/ _ \ \ / / |/ __/ _ \
# | |  | | (_) | (_| |  __/ | | |_| |  __/\ V /| | (_|  __/
# |_|  |_|\___/ \__,_|\___|_| |____/ \___| \_/ |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from dataclasses import (
    asdict, dataclass, field
)


@dataclass(slots=True)
class Attachment:
    kind: str
    local: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    url: str | None = None
    extra: dict[str, typing.Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, typing.Any]:
        payload = asdict(self)
        if not payload["extra"]:
            payload.pop("extra", None)
        return {k: v for k, v in payload.items() if v is not None}


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
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    data: dict[str, typing.Any] | None = None
    logs: list[str] = field(default_factory=list)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        attachments: list[Attachment] | None = None,
        data: dict[str, typing.Any] | None = None,
        logs: list[str] | None = None,
    ) -> "SemanticResult":
        return cls(
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
        payload: dict[str, typing.Any] = {"text": self.text}

        if self.attachments:
            payload["attachments"] = [
                item.to_dict() if isinstance(item, Attachment) else item
                for item in self.attachments
            ]

        if self.data:
            payload["data"] = self.data

        if self.logs:
            payload["logs"] = self.logs

        return payload


if __name__ == '__main__':
    pass
