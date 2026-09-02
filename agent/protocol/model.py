# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field
)

from .json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

ModelStreamEndReason: typing.TypeAlias = typing.Literal[
    "settled",
    "fatal",
    "cancelled",
    "protocol_error",
]

_RESERVED_MODEL_OPTIONS = frozenset({
    "attachments",
    "cid",
    "environment_snapshot",
    "exec_env",
    "message",
    "metadata",
    "on_approval_snapshot",
    "on_reconnect_status",
    "pref_config",
    "sid",
    "timeout",
    "tools",
    "turn_id",
})

_RESERVED_MODEL_METADATA = frozenset({
    "cid",
    "sid",
    "turn_id"
})


@dataclass(frozen=True, slots=True)
class ModelStreamRequest:
    """描述一次不携带运行时回调的可序列化模型流请求。"""

    cid: str
    sid: str
    turn_id: str
    pref_config: Mapping[str, JsonValue]
    message: str
    tools: tuple[Mapping[str, JsonValue], ...]
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    environment_snapshot: Mapping[str, JsonValue] | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    options: Mapping[str, JsonValue] = field(default_factory=dict)
    timeout: float = 60.0

    def __post_init__(self) -> None:
        """校验请求字段并冻结全部 JSON 兼容输入。"""
        for field_name in ("cid", "sid", "turn_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"model {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.message, str):
            raise TypeError("model message must be a string")
        if not isinstance(self.pref_config, Mapping):
            raise TypeError("model pref_config must be an object")
        if not isinstance(self.tools, (tuple, list)):
            raise TypeError("model tools must be a sequence")
        if not isinstance(self.attachments, (tuple, list)):
            raise TypeError("model attachments must be a sequence")
        if (
            self.environment_snapshot is not None
            and not isinstance(self.environment_snapshot, Mapping)
        ):
            raise TypeError("model environment snapshot must be an object")
        if not isinstance(self.options, Mapping):
            raise TypeError("model options must be an object")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("model metadata must be an object")
        reserved_options = _RESERVED_MODEL_OPTIONS.intersection(self.options)
        if reserved_options:
            names = ", ".join(sorted(reserved_options))
            raise ValueError(f"model options contain reserved fields: {names}")
        reserved_metadata = _RESERVED_MODEL_METADATA.intersection(self.metadata)
        if reserved_metadata:
            names = ", ".join(sorted(reserved_metadata))
            raise ValueError(f"model metadata contains reserved fields: {names}")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(float(self.timeout))
            or float(self.timeout) <= 0
        ):
            raise ValueError("model timeout must be a positive finite number")
        frozen_config = freeze_json(
            dict(self.pref_config),
            field_name="pref_config",
        )
        frozen_options = freeze_json(
            dict(self.options),
            field_name="model options",
        )
        frozen_metadata = freeze_json(
            dict(self.metadata),
            field_name="model metadata",
        )
        frozen_environment: Mapping[str, JsonValue] | None = None
        if self.environment_snapshot is not None:
            environment_value = freeze_json(
                dict(self.environment_snapshot),
                field_name="model environment snapshot",
            )
            if not isinstance(environment_value, Mapping):
                raise TypeError("model environment snapshot must be an object")
            frozen_environment = environment_value
        if not isinstance(frozen_config, Mapping):
            raise TypeError("model pref_config must be an object")
        if not isinstance(frozen_options, Mapping):
            raise TypeError("model options must be an object")
        if not isinstance(frozen_metadata, Mapping):
            raise TypeError("model metadata must be an object")

        object.__setattr__(self, "pref_config", frozen_config)
        object.__setattr__(
            self,
            "tools",
            _freeze_objects(self.tools, field_name="tools"),
        )
        object.__setattr__(
            self,
            "attachments",
            _freeze_objects(self.attachments, field_name="attachments"),
        )
        object.__setattr__(
            self,
            "environment_snapshot",
            frozen_environment,
        )
        object.__setattr__(self, "options", frozen_options)
        object.__setattr__(self, "metadata", frozen_metadata)
        object.__setattr__(self, "timeout", float(self.timeout))

    def pref_config_value(self) -> dict[str, ThawedJsonValue]:
        """返回远端 adapter 可消费的独立模型配置。"""
        return thaw_object(self.pref_config, field_name="pref_config")

    def tool_values(self) -> list[dict[str, ThawedJsonValue]]:
        """返回远端 adapter 可消费的独立工具列表。"""
        return [
            thaw_object(item, field_name="tools")
            for item in self.tools
        ]

    def attachment_values(self) -> list[dict[str, ThawedJsonValue]]:
        """返回远端 adapter 可消费的独立附件列表。"""
        return [
            thaw_object(item, field_name="attachments")
            for item in self.attachments
        ]

    def environment_snapshot_value(
        self,
    ) -> dict[str, ThawedJsonValue] | None:
        """返回远端 adapter 可消费的独立环境快照。"""
        if self.environment_snapshot is None:
            return None
        return thaw_object(
            self.environment_snapshot,
            field_name="model environment snapshot",
        )

    def option_values(self) -> dict[str, ThawedJsonValue]:
        """返回远端 adapter 可消费的独立扩展参数。"""
        return thaw_object(self.options, field_name="model options")

    def metadata_value(self) -> dict[str, ThawedJsonValue]:
        """返回不包含协议坐标的独立请求元数据。"""
        return thaw_object(self.metadata, field_name="model metadata")


def _freeze_objects(
    values: typing.Iterable[Mapping[str, typing.Any]],
    *,
    field_name: str,
) -> tuple[Mapping[str, JsonValue], ...]:
    """校验并冻结对象序列。"""
    frozen_values: list[Mapping[str, JsonValue]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError(f"model {field_name} entries must be objects")
        frozen = freeze_json(dict(value), field_name=field_name)
        if not isinstance(frozen, Mapping):
            raise TypeError(f"model {field_name} entries must be objects")
        frozen_values.append(frozen)
    return tuple(frozen_values)


if __name__ == '__main__':
    pass
