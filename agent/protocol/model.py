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
    thaw_json,
    thaw_object,
)

ModelStreamEndReason: typing.TypeAlias = typing.Literal[
    "settled",
    "fatal",
    "cancelled",
    "protocol_error",
]

RemoteRequestKind: typing.TypeAlias = typing.Literal[
    "mind_chat",
    "mind_review",
]

_RESERVED_MODEL_OPTIONS = frozenset({
    "attachments",
    "cid",
    "environment_snapshot",
    "exec_env",
    "message",
    "metadata",
    "on_approval_snapshot",
    "on_recovery_status",
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

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回可由本地恢复账本持久化的完整冻结请求。"""
        return {
            "cid": self.cid,
            "sid": self.sid,
            "turn_id": self.turn_id,
            "pref_config": thaw_json(self.pref_config),
            "message": self.message,
            "tools": thaw_json(self.tools),
            "attachments": thaw_json(self.attachments),
            "environment_snapshot": (
                thaw_json(self.environment_snapshot)
                if self.environment_snapshot is not None
                else None
            ),
            "metadata": thaw_json(self.metadata),
            "options": thaw_json(self.options),
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, ThawedJsonValue],
    ) -> "ModelStreamRequest":
        """从本地恢复账本还原并重新校验完整模型请求。"""
        expected = {
            "cid",
            "sid",
            "turn_id",
            "pref_config",
            "message",
            "tools",
            "attachments",
            "environment_snapshot",
            "metadata",
            "options",
            "timeout",
        }
        if set(value) != expected:
            raise ValueError("persisted model request fields are invalid")

        pref_config = value.get("pref_config")
        tools = value.get("tools")
        attachments = value.get("attachments")
        environment_snapshot = value.get("environment_snapshot")
        metadata = value.get("metadata")
        options = value.get("options")
        if not isinstance(pref_config, dict):
            raise TypeError("persisted model pref_config must be an object")
        if not isinstance(tools, list):
            raise TypeError("persisted model tools must be a sequence")
        if not isinstance(attachments, list):
            raise TypeError("persisted model attachments must be a sequence")
        if environment_snapshot is not None and not isinstance(
            environment_snapshot,
            dict,
        ):
            raise TypeError(
                "persisted model environment snapshot must be an object"
            )
        if not isinstance(metadata, dict):
            raise TypeError("persisted model metadata must be an object")
        if not isinstance(options, dict):
            raise TypeError("persisted model options must be an object")

        cid = value.get("cid")
        sid = value.get("sid")
        turn_id = value.get("turn_id")
        message = value.get("message")
        timeout = value.get("timeout")
        if not isinstance(cid, str):
            raise TypeError("persisted model cid must be a string")
        if not isinstance(sid, str):
            raise TypeError("persisted model sid must be a string")
        if not isinstance(turn_id, str):
            raise TypeError("persisted model turn_id must be a string")
        if not isinstance(message, str):
            raise TypeError("persisted model message must be a string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("persisted model timeout must be a number")

        return cls(
            cid=cid,
            sid=sid,
            turn_id=turn_id,
            pref_config=pref_config,
            message=message,
            tools=tuple(_object_sequence(tools, field_name="tools")),
            attachments=tuple(
                _object_sequence(attachments, field_name="attachments")
            ),
            environment_snapshot=environment_snapshot,
            metadata=metadata,
            options=options,
            timeout=float(timeout),
        )


@dataclass(frozen=True, slots=True)
class ReviewStreamRequest:
    """描述一次与传输实现无关的冻结 Review 请求。"""

    request_id: str
    cid: str
    sid: str
    turn_id: str
    target: Mapping[str, JsonValue]
    workspace: Mapping[str, JsonValue]
    execution: Mapping[str, JsonValue]
    delivery: typing.Literal["inline"] = "inline"

    def __post_init__(self) -> None:
        """校验本地身份、只读执行边界并冻结结构化载荷。"""
        for field_name in ("request_id", "cid", "sid", "turn_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"review {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if self.delivery != "inline":
            raise ValueError("local review delivery must be inline")

        target = _freeze_object(self.target, field_name="review target")
        workspace = _freeze_object(
            self.workspace,
            field_name="review workspace",
        )
        execution = _freeze_object(
            self.execution,
            field_name="review execution",
        )
        _validate_review_execution(execution)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "execution", execution)

    @property
    def has_workspace_content(self) -> bool:
        """返回冻结工作区是否携带补丁或文件内容。"""
        patch = self.workspace.get("patch")
        files = self.workspace.get("files")
        return bool(
            isinstance(patch, str) and patch
            or isinstance(files, tuple) and files
        )

    @property
    def has_read_only_tools(self) -> bool:
        """返回执行快照是否声明至少一个只读客户端工具。"""
        tools = self.execution.get("tools")
        return isinstance(tools, tuple) and bool(tools)

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回可供协议 adapter 和恢复账本消费的完整请求。"""
        return {
            "request_id": self.request_id,
            "cid": self.cid,
            "sid": self.sid,
            "turn_id": self.turn_id,
            "target": thaw_json(self.target),
            "delivery": self.delivery,
            "workspace": thaw_json(self.workspace),
            "execution": thaw_json(self.execution),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, ThawedJsonValue],
    ) -> "ReviewStreamRequest":
        """从持久化字典还原并重新校验冻结 Review 请求。"""
        expected = {
            "request_id",
            "cid",
            "sid",
            "turn_id",
            "target",
            "delivery",
            "workspace",
            "execution",
        }
        if set(value) != expected:
            raise ValueError("persisted review request fields are invalid")
        identities: dict[str, str] = {}
        for field_name in ("request_id", "cid", "sid", "turn_id"):
            field_value = value.get(field_name)
            if not isinstance(field_value, str):
                raise TypeError(
                    f"persisted review {field_name} must be a string"
                )
            identities[field_name] = field_value
        target = value.get("target")
        workspace = value.get("workspace")
        execution = value.get("execution")
        if not isinstance(target, dict):
            raise TypeError("persisted review target must be an object")
        if not isinstance(workspace, dict):
            raise TypeError("persisted review workspace must be an object")
        if not isinstance(execution, dict):
            raise TypeError("persisted review execution must be an object")
        delivery = value.get("delivery")
        if delivery != "inline":
            raise ValueError("persisted review delivery must be inline")
        return cls(
            request_id=identities["request_id"],
            cid=identities["cid"],
            sid=identities["sid"],
            turn_id=identities["turn_id"],
            target=target,
            workspace=workspace,
            execution=execution,
            delivery="inline",
        )


RemoteStreamRequest: typing.TypeAlias = ModelStreamRequest | ReviewStreamRequest


def remote_request_kind(request: RemoteStreamRequest) -> RemoteRequestKind:
    """返回冻结远端请求的持久化判别值。"""
    if isinstance(request, ModelStreamRequest):
        return "mind_chat"
    if isinstance(request, ReviewStreamRequest):
        return "mind_review"
    raise TypeError("remote stream request is invalid")


def remote_request_from_dict(
    kind: RemoteRequestKind,
    value: Mapping[str, ThawedJsonValue],
) -> RemoteStreamRequest:
    """按持久化判别值还原正式远端请求联合。"""
    if kind == "mind_chat":
        return ModelStreamRequest.from_dict(value)
    if kind == "mind_review":
        return ReviewStreamRequest.from_dict(value)
    raise ValueError("persisted remote request kind is invalid")


@dataclass(frozen=True, slots=True)
class TurnObservationRequest:
    """描述只观察已提交远端 Turn 的稳定坐标与超时。"""

    cid: str
    sid: str
    turn_id: str
    timeout: float = 60.0
    after_event_seq: int | None = None
    replay_target_seq: int | None = None

    def __post_init__(self) -> None:
        """校验观察坐标和有限超时。"""
        for field_name in ("cid", "sid", "turn_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"turn observation {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(float(self.timeout))
            or float(self.timeout) <= 0
        ):
            raise ValueError(
                "turn observation timeout must be a positive finite number"
            )
        for field_name in ("after_event_seq", "replay_target_seq"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"turn observation {field_name} must be non-negative"
                )
        if (
            self.after_event_seq is not None
            and self.replay_target_seq is not None
            and self.replay_target_seq < self.after_event_seq
        ):
            raise ValueError(
                "turn observation replay target precedes its event cursor"
            )
        object.__setattr__(self, "timeout", float(self.timeout))


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


def _freeze_object(
    value: Mapping[str, JsonValue],
    *,
    field_name: str,
) -> Mapping[str, JsonValue]:
    """校验并冻结一个具名 JSON 对象。"""
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    frozen = freeze_json(dict(value), field_name=field_name)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return frozen


def _validate_review_execution(execution: Mapping[str, JsonValue]) -> None:
    """拒绝 Review 请求中的普通输入和可写执行能力。"""
    expected = {
        "llm_conf",
        "additional_context",
        "system_message",
        "attachments",
        "streaming",
        "tools",
        "hosted_tools",
        "skills",
        "sandbox_mode",
        "metadata",
    }
    if set(execution) != expected:
        raise ValueError("review execution fields are invalid")
    fixed_values: tuple[tuple[str, JsonValue], ...] = (
        ("additional_context", ()),
        ("system_message", ""),
        ("attachments", None),
        ("streaming", False),
        ("hosted_tools", None),
        ("skills", None),
        ("sandbox_mode", "read-only"),
    )
    for field_name, expected_value in fixed_values:
        if execution.get(field_name) != expected_value:
            raise ValueError(f"review execution {field_name} is invalid")
    if not isinstance(execution.get("llm_conf"), Mapping):
        raise TypeError("review execution llm_conf must be an object")
    metadata = execution.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("review execution metadata must be an object or null")
    tools = execution.get("tools")
    if tools is None:
        return
    if not isinstance(tools, tuple):
        raise TypeError("review execution tools must be a sequence or null")
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise TypeError("review execution tool must be an object")
        annotations = tool.get("annotations")
        if (
            not isinstance(annotations, Mapping)
            or annotations.get("readOnlyHint") is not True
        ):
            raise ValueError(
                "review execution tools must declare "
                "annotations.readOnlyHint=true"
            )
def _object_sequence(
    values: typing.Iterable[ThawedJsonValue],
    *,
    field_name: str,
) -> tuple[dict[str, ThawedJsonValue], ...]:
    """校验恢复载荷中的对象序列。"""
    objects: list[dict[str, ThawedJsonValue]] = []
    for value in values:
        if not isinstance(value, dict):
            raise TypeError(f"persisted model {field_name} entries must be objects")
        objects.append(dict(value))
    return tuple(objects)


if __name__ == '__main__':
    pass
