# -*- coding: utf-8 -*-
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


if __name__ == '__main__':
    pass
