# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from dataclasses import (
    dataclass,
    field,
)
from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class TurnInput(object):
    """描述一个可关联和重放的轮次输入。"""
    client_message_id: str
    text: str = ""
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_message_id",
            str(self.client_message_id or "").strip(),
        )
        object.__setattr__(self, "text", str(self.text or "").strip())
        object.__setattr__(
            self,
            "attachments",
            tuple(copy.deepcopy(item) for item in self.attachments),
        )
        object.__setattr__(self, "extras", copy.deepcopy(dict(self.extras)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, typing.Any]) -> "TurnInput":
        """校验协议对象并创建轮次输入。"""
        client_message_id = str(value.get("client_message_id") or "").strip()
        text = value.get("text")
        attachments = value.get("attachments", [])
        extras = value.get("extras", {})

        if not client_message_id:
            raise ValueError("turn input client_message_id is required")
        if not isinstance(text, str):
            raise TypeError("turn input text must be a string")
        if not isinstance(attachments, list):
            raise TypeError("turn input attachments must be a list")
        if any(not isinstance(item, dict) for item in attachments):
            raise TypeError("turn input attachments must contain objects")
        if not isinstance(extras, dict):
            raise TypeError("turn input extras must be an object")
        if not text.strip() and not attachments:
            raise ValueError("turn input text or attachments is required")

        return cls(
            client_message_id=client_message_id,
            text=text,
            attachments=tuple(attachments),
            extras=extras,
        )

    def request_input(self) -> dict[str, typing.Any]:
        """返回控制请求使用的输入载荷。"""
        return {
            "text": self.text,
            "attachments": [copy.deepcopy(item) for item in self.attachments],
            "extras": copy.deepcopy(self.extras),
        }


if __name__ == '__main__':
    pass
