#  __  __           _      _   ____             _
# |  \/  | ___   __| | ___| | |  _ \  _____   _(_) ___ ___
# | |\/| |/ _ \ / _` |/ _ \ | | | | |/ _ \ \ / / |/ __/ _ \
# | |  | | (_) | (_| |  __/ | | |_| |  __/\ V /| | (_|  __/
# |_|  |_|\___/ \__,_|\___|_| |____/ \___| \_/ |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from dataclasses import (
    dataclass, field, asdict
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
        data = asdict(self)
        if not data["extra"]:
            data.pop("extra", None)
        return data


@dataclass(slots=True)
class PrimitiveResult:
    ok: bool
    raw: str = ""

    def to_dict(self) -> dict[str, typing.Any]:
        return asdict(self)


@dataclass(slots=True)
class ComboResult:
    ok: bool
    reason: str | None = None
    data: dict[str, typing.Any] | None = None

    def to_dict(self) -> dict[str, typing.Any]:
        return asdict(self)


@dataclass(slots=True)
class SemanticResult:
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    data: dict[str, typing.Any] | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "text"        : self.text,
            "attachments" : [a.to_dict() if isinstance(a, Attachment) else a for a in self.attachments],
            "data"        : self.data,
            "logs"        : self.logs
        }


if __name__ == '__main__':
    pass
