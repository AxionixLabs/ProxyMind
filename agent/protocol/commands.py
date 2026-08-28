# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import uuid
import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field
)

from .json_value import (
    JsonValue,
    freeze_json,
    thaw_json
)


@dataclass(frozen=True, slots=True)
class SubmitTurnCommand:
    """描述一次本地主动 Turn 提交及其稳定幂等身份。"""

    command_id: str
    session_id: str
    run_id: str
    message: str
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    pref_config: Mapping[str, JsonValue] | None = None
    extras: Mapping[str, JsonValue] | None = None
    idempotency_key: str = ""
    causation_id: str | None = None
    trace_context: Mapping[str, JsonValue] = field(default_factory=dict)
    kind: typing.Literal["submit_turn"] = field(
        default="submit_turn",
        init=False,
    )

    def __post_init__(self) -> None:
        """校验命令身份并冻结全部可序列化载荷。"""
        for field_name in ("command_id", "session_id", "run_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())

        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        if not isinstance(self.attachments, (tuple, list)):
            raise TypeError("attachments must be a sequence")

        frozen_attachments: list[Mapping[str, JsonValue]] = []
        for attachment in self.attachments:
            if not isinstance(attachment, Mapping):
                raise TypeError("attachment must be an object")
            frozen = freeze_json(dict(attachment), field_name="attachments")
            if not isinstance(frozen, Mapping):
                raise TypeError("attachment must be an object")
            frozen_attachments.append(frozen)

        frozen_pref: Mapping[str, JsonValue] | None = None
        if self.pref_config is not None:
            if not isinstance(self.pref_config, Mapping):
                raise TypeError("pref_config must be an object")
            pref_value = freeze_json(
                dict(self.pref_config),
                field_name="pref_config",
            )
            if not isinstance(pref_value, Mapping):
                raise TypeError("pref_config must be an object")
            frozen_pref = pref_value

        frozen_extras: Mapping[str, JsonValue] | None = None
        if self.extras is not None:
            if not isinstance(self.extras, Mapping):
                raise TypeError("extras must be an object")
            extras_value = freeze_json(
                dict(self.extras),
                field_name="extras",
            )
            if not isinstance(extras_value, Mapping):
                raise TypeError("extras must be an object")
            frozen_extras = extras_value

        if not isinstance(self.trace_context, Mapping):
            raise TypeError("trace_context must be an object")
        trace_value = freeze_json(
            dict(self.trace_context),
            field_name="trace_context",
        )
        if not isinstance(trace_value, Mapping):
            raise TypeError("trace_context must be an object")

        idempotency_key = str(self.idempotency_key or "").strip()
        causation_id = str(self.causation_id or "").strip() or None

        object.__setattr__(self, "attachments", tuple(frozen_attachments))
        object.__setattr__(self, "pref_config", frozen_pref)
        object.__setattr__(self, "extras", frozen_extras)
        object.__setattr__(self, "trace_context", trace_value)
        object.__setattr__(
            self,
            "idempotency_key",
            idempotency_key or self.command_id,
        )
        object.__setattr__(self, "causation_id", causation_id)

    @classmethod
    def create(
        cls,
        *,
        message: str,
        attachments: typing.Iterable[Mapping[str, typing.Any]] = (),
        pref_config: Mapping[str, typing.Any] | None = None,
        extras: Mapping[str, typing.Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        command_id: str | None = None,
        idempotency_key: str | None = None,
        causation_id: str | None = None,
        trace_context: Mapping[str, typing.Any] | None = None,
    ) -> "SubmitTurnCommand":
        """创建具有本地稳定身份的主动 Turn 命令。"""
        resolved_command_id = command_id or _new_id("cmd")
        return cls(
            command_id=resolved_command_id,
            session_id=session_id or _new_id("session"),
            run_id=run_id or _new_id("run"),
            message=message,
            attachments=tuple(attachments),
            pref_config=pref_config,
            extras=extras,
            idempotency_key=idempotency_key or resolved_command_id,
            causation_id=causation_id,
            trace_context=trace_context or {},
        )

    def to_dict(self) -> dict[str, typing.Any]:
        """返回不包含运行时对象的协议字典。"""
        payload: dict[str, typing.Any] = {
            "message": self.message,
            "attachments": [thaw_json(item) for item in self.attachments],
        }
        if self.pref_config is not None:
            payload["pref_config"] = thaw_json(self.pref_config)
        if self.extras is not None:
            payload["extras"] = thaw_json(self.extras)

        return {
            "command_id": self.command_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": payload,
            "idempotency_key": self.idempotency_key,
            "causation_id": self.causation_id,
            "trace_context": thaw_json(self.trace_context),
        }

    def fingerprint(self) -> str:
        """返回忽略 command_id 的确定性意图指纹。"""
        value = self.to_dict()
        value.pop("command_id")
        value.pop("causation_id")
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def attachment_values(self) -> list[dict[str, typing.Any]]:
        """返回旧 Turn adapter 可消费的独立附件副本。"""
        return [
            typing.cast(dict[str, typing.Any], thaw_json(item))
            for item in self.attachments
        ]

    def pref_config_value(self) -> dict[str, typing.Any] | None:
        """返回旧 Turn adapter 可消费的独立配置副本。"""
        if self.pref_config is None:
            return None
        return typing.cast(
            dict[str, typing.Any],
            thaw_json(self.pref_config),
        )

    def extras_value(self) -> dict[str, typing.Any] | None:
        """返回旧 Turn adapter 可消费的独立扩展输入副本。"""
        if self.extras is None:
            return None
        return typing.cast(
            dict[str, typing.Any],
            thaw_json(self.extras),
        )


def _new_id(prefix: str) -> str:
    """生成带职责前缀的本地随机身份。"""
    return f"{prefix}_{uuid.uuid4().hex}"


if __name__ == '__main__':
    pass
