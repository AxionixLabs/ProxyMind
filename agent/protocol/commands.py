# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing
import uuid
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
from .model import ReviewStreamRequest

TurnControlStatus: typing.TypeAlias = typing.Literal[
    "accepted",
    "turn_not_active",
    "turn_not_steerable",
    "turn_mismatch",
    "duplicate",
]

TurnRuntimeStatus: typing.TypeAlias = typing.Literal[
    "queued",
    "running",
    "waiting_tool",
    "waiting_approval",
    "waiting_user",
    "reconciliation_required",
    "finalizing",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
]

PromptSource: typing.TypeAlias = typing.Literal["none", "server", "client"]


@dataclass(frozen=True, slots=True)
class TurnControlReceipt:
    """描述 Protocol Client 对中断等轮次控制命令的稳定回执。"""

    status: TurnControlStatus
    request_id: str
    turn_id: str
    client_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class SteerTurnInput:
    """描述一项可幂等提交到活动 Turn 的引导输入。"""

    client_message_id: str
    text: str = ""
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    extras: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验并冻结输入正文、附件和扩展字段。"""
        client_message_id = str(self.client_message_id or "").strip()
        if not client_message_id:
            raise ValueError("steer input client_message_id is required")
        if not isinstance(self.text, str):
            raise TypeError("steer input text must be a string")
        if not isinstance(self.attachments, (tuple, list)):
            raise TypeError("steer input attachments must be a sequence")
        if not isinstance(self.extras, Mapping):
            raise TypeError("steer input extras must be an object")

        frozen_attachments: list[Mapping[str, JsonValue]] = []
        for attachment in self.attachments:
            if not isinstance(attachment, Mapping):
                raise TypeError("steer input attachment must be an object")
            frozen = freeze_json(
                dict(attachment),
                field_name="steer input attachments",
            )
            if not isinstance(frozen, Mapping):
                raise TypeError("steer input attachment must be an object")
            frozen_attachments.append(frozen)

        frozen_extras = freeze_json(
            dict(self.extras),
            field_name="steer input extras",
        )
        if not isinstance(frozen_extras, Mapping):
            raise TypeError("steer input extras must be an object")

        if not self.text.strip() and not frozen_attachments:
            raise ValueError("steer input text or attachments is required")

        object.__setattr__(self, "client_message_id", client_message_id)
        object.__setattr__(self, "attachments", tuple(frozen_attachments))
        object.__setattr__(self, "extras", frozen_extras)

    def request_values(self) -> dict[str, typing.Any]:
        """返回发送到 Protocol Client 的独立输入副本。"""
        return {
            "client_message_id": self.client_message_id,
            "text": self.text,
            "attachments": [
                thaw_object(item, field_name="steer input attachments")
                for item in self.attachments
            ],
            "extras": thaw_object(self.extras, field_name="steer input extras"),
        }


@dataclass(frozen=True, slots=True)
class TurnCompletedSnapshot:
    """描述与服务端唯一终态事件同构的稳定快照。"""

    turn_id: str
    status: typing.Literal["completed", "failed", "interrupted", "cancelled"]
    error: str | None
    last_event_seq: int
    completed_at: float
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TurnReconcileReceipt:
    """描述未确认引导输入在服务端的权威归属。"""

    turn_id: str
    turn_exists: bool
    terminal: TurnCompletedSnapshot | None
    committed_ids: tuple[str, ...] = ()
    pending_ids: tuple[str, ...] = ()
    retry_ids: tuple[str, ...] = ()
    unknown_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TurnStatusSnapshot:
    """描述服务端持久化 Turn 的稳定状态快照。"""

    cid: str
    sid: str
    turn_id: str
    run_id: str
    status: TurnRuntimeStatus
    terminal: TurnCompletedSnapshot | None
    attempt: int
    version: int
    last_event_seq: int
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class ForkPrompt:
    """描述分支接口返回的可重新提交输入。"""

    message: str
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    extras: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验并冻结分支输入。"""
        if not isinstance(self.message, str):
            raise TypeError("fork prompt message must be a string")
        if not isinstance(self.attachments, (tuple, list)):
            raise TypeError("fork prompt attachments must be a sequence")
        if not isinstance(self.extras, Mapping):
            raise TypeError("fork prompt extras must be an object")
        frozen_attachments: list[Mapping[str, JsonValue]] = []
        for attachment in self.attachments:
            if not isinstance(attachment, Mapping):
                raise TypeError("fork prompt attachment must be an object")
            frozen = freeze_json(
                dict(attachment),
                field_name="fork prompt attachments",
            )
            if not isinstance(frozen, Mapping):
                raise TypeError("fork prompt attachment must be an object")
            frozen_attachments.append(frozen)
        frozen_extras = freeze_json(
            dict(self.extras),
            field_name="fork prompt extras",
        )
        if not isinstance(frozen_extras, Mapping):
            raise TypeError("fork prompt extras must be an object")
        object.__setattr__(self, "attachments", tuple(frozen_attachments))
        object.__setattr__(self, "extras", frozen_extras)

    def values(self) -> dict[str, typing.Any]:
        """返回可重建本地输入状态的独立副本。"""
        return {
            "message": self.message,
            "attachments": [
                thaw_object(item, field_name="fork prompt attachments")
                for item in self.attachments
            ],
            "extras": thaw_object(self.extras, field_name="fork prompt extras"),
        }


@dataclass(frozen=True, slots=True)
class ConversationForkReceipt:
    """描述服务端会话分支命令的稳定结果。"""

    request_id: str
    source_cid: str
    source_sid: str
    prompt_source: PromptSource
    cid: str
    sid: str
    copied_items: int
    copied_turns: int | None = None
    before_turn_id: str | None = None
    prompt: ForkPrompt | None = None


@dataclass(frozen=True, slots=True)
class SubmitTurnCommand:
    """描述一次本地主动 Turn 提交及其稳定幂等身份。"""

    command_id: str
    session_id: str
    run_id: str
    message: str
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    environment_snapshot: Mapping[str, JsonValue] | None = None
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

        frozen_environment: Mapping[str, JsonValue] | None = None
        if self.environment_snapshot is not None:
            if not isinstance(self.environment_snapshot, Mapping):
                raise TypeError("environment_snapshot must be an object")
            environment_value = freeze_json(
                dict(self.environment_snapshot),
                field_name="environment_snapshot",
            )
            if not isinstance(environment_value, Mapping):
                raise TypeError("environment_snapshot must be an object")
            frozen_environment = environment_value

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
        object.__setattr__(
            self,
            "environment_snapshot",
            frozen_environment,
        )
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
        environment_snapshot: Mapping[str, typing.Any] | None = None,
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
            environment_snapshot=environment_snapshot,
            pref_config=pref_config,
            extras=extras,
            idempotency_key=idempotency_key or resolved_command_id,
            causation_id=causation_id,
            trace_context=trace_context or {},
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, typing.Any],
    ) -> "SubmitTurnCommand":
        """从持久化协议字典还原并重新校验主动 Turn 命令。"""
        if not isinstance(value, Mapping):
            raise TypeError("command must be an object")
        if value.get("kind") != "submit_turn":
            raise ValueError("unsupported command kind")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError("command payload must be an object")
        attachments = payload.get("attachments", ())
        if not isinstance(attachments, (tuple, list)):
            raise TypeError("command attachments must be a sequence")
        return cls(
            command_id=value.get("command_id"),
            session_id=value.get("session_id"),
            run_id=value.get("run_id"),
            message=payload.get("message"),
            attachments=tuple(attachments),
            environment_snapshot=payload.get("environment_snapshot"),
            pref_config=payload.get("pref_config"),
            extras=payload.get("extras"),
            idempotency_key=value.get("idempotency_key"),
            causation_id=value.get("causation_id"),
            trace_context=value.get("trace_context") or {},
        )

    def to_dict(self) -> dict[str, typing.Any]:
        """返回不包含运行时对象的协议字典。"""
        payload: dict[str, typing.Any] = {
            "message": self.message,
            "attachments": [thaw_json(item) for item in self.attachments],
        }
        if self.pref_config is not None:
            payload["pref_config"] = thaw_json(self.pref_config)
        if self.environment_snapshot is not None:
            payload["environment_snapshot"] = thaw_json(
                self.environment_snapshot
            )
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
        """返回忽略 command_id 的 SHA-256 意图指纹。"""
        return _command_fingerprint(self.to_dict())

    def attachment_values(self) -> list[dict[str, ThawedJsonValue]]:
        """返回旧 Turn adapter 可消费的独立附件副本。"""
        return [
            thaw_object(item, field_name="attachments")
            for item in self.attachments
        ]

    def pref_config_value(self) -> dict[str, ThawedJsonValue] | None:
        """返回旧 Turn adapter 可消费的独立配置副本。"""
        if self.pref_config is None:
            return None
        return thaw_object(self.pref_config, field_name="pref_config")

    def environment_snapshot_value(
        self,
    ) -> dict[str, ThawedJsonValue] | None:
        """返回执行 adapter 可消费的独立环境快照副本。"""
        if self.environment_snapshot is None:
            return None
        return thaw_object(
            self.environment_snapshot,
            field_name="environment_snapshot",
        )

    def extras_value(self) -> dict[str, ThawedJsonValue] | None:
        """返回旧 Turn adapter 可消费的独立扩展输入副本。"""
        if self.extras is None:
            return None
        return thaw_object(self.extras, field_name="extras")


@dataclass(frozen=True, slots=True)
class SubmitReviewCommand:
    """描述一次本地 Review 提交及其完整冻结远端意图。"""

    command_id: str
    session_id: str
    run_id: str
    request: ReviewStreamRequest
    environment_snapshot: Mapping[str, JsonValue] | None = None
    idempotency_key: str = ""
    causation_id: str | None = None
    trace_context: Mapping[str, JsonValue] = field(default_factory=dict)
    kind: typing.Literal["submit_review"] = field(
        default="submit_review",
        init=False,
    )

    def __post_init__(self) -> None:
        """校验本地身份并冻结 Review 执行环境和追踪上下文。"""
        for field_name in ("command_id", "session_id", "run_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.request, ReviewStreamRequest):
            raise TypeError("review request is required")

        environment = _optional_frozen_object(
            self.environment_snapshot,
            field_name="review environment_snapshot",
        )
        trace_context = _required_frozen_object(
            self.trace_context,
            field_name="review trace_context",
        )
        idempotency_key = str(self.idempotency_key or "").strip()
        causation_id = str(self.causation_id or "").strip() or None
        object.__setattr__(self, "environment_snapshot", environment)
        object.__setattr__(self, "trace_context", trace_context)
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
        request: ReviewStreamRequest,
        environment_snapshot: Mapping[str, JsonValue] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        command_id: str | None = None,
        idempotency_key: str | None = None,
        causation_id: str | None = None,
        trace_context: Mapping[str, JsonValue] | None = None,
    ) -> "SubmitReviewCommand":
        """创建具有本地稳定身份的 Review 命令。"""
        resolved_command_id = command_id or _new_id("cmd")
        return cls(
            command_id=resolved_command_id,
            session_id=session_id or _new_id("session"),
            run_id=run_id or _new_id("run"),
            request=request,
            environment_snapshot=environment_snapshot,
            idempotency_key=idempotency_key or resolved_command_id,
            causation_id=causation_id,
            trace_context=trace_context or {},
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, ThawedJsonValue],
    ) -> "SubmitReviewCommand":
        """从持久化协议字典还原并重新校验 Review 命令。"""
        expected = {
            "command_id",
            "session_id",
            "run_id",
            "kind",
            "payload",
            "idempotency_key",
            "causation_id",
            "trace_context",
        }
        if set(value) != expected or value.get("kind") != "submit_review":
            raise ValueError("persisted review command fields are invalid")
        payload = value.get("payload")
        if not isinstance(payload, dict) or set(payload) != {
            "request",
            "environment_snapshot",
        }:
            raise ValueError("persisted review command payload is invalid")
        request = payload.get("request")
        environment = payload.get("environment_snapshot")
        trace_context = value.get("trace_context")
        if not isinstance(request, dict):
            raise TypeError("persisted review request must be an object")
        if environment is not None and not isinstance(environment, dict):
            raise TypeError("persisted review environment must be an object")
        if not isinstance(trace_context, dict):
            raise TypeError("persisted review trace_context must be an object")
        command_id = value.get("command_id")
        session_id = value.get("session_id")
        run_id = value.get("run_id")
        idempotency_key = value.get("idempotency_key")
        causation_id = value.get("causation_id")
        for field_name, field_value in (
            ("command_id", command_id),
            ("session_id", session_id),
            ("run_id", run_id),
            ("idempotency_key", idempotency_key),
        ):
            if not isinstance(field_value, str):
                raise TypeError(f"persisted review {field_name} must be text")
        if causation_id is not None and not isinstance(causation_id, str):
            raise TypeError("persisted review causation_id must be text or null")
        return cls(
            command_id=command_id,
            session_id=session_id,
            run_id=run_id,
            request=ReviewStreamRequest.from_dict(request),
            environment_snapshot=environment,
            idempotency_key=idempotency_key,
            causation_id=causation_id,
            trace_context=trace_context,
        )

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回不包含运行时对象的 Review 命令字典。"""
        return {
            "command_id": self.command_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": {
                "request": self.request.to_dict(),
                "environment_snapshot": (
                    thaw_json(self.environment_snapshot)
                    if self.environment_snapshot is not None
                    else None
                ),
            },
            "idempotency_key": self.idempotency_key,
            "causation_id": self.causation_id,
            "trace_context": thaw_json(self.trace_context),
        }

    def fingerprint(self) -> str:
        """返回忽略 command_id 的 SHA-256 Review 意图指纹。"""
        return _command_fingerprint(self.to_dict())

    def environment_snapshot_value(
        self,
    ) -> dict[str, ThawedJsonValue] | None:
        """返回 Review 执行 adapter 可消费的环境快照副本。"""
        if self.environment_snapshot is None:
            return None
        return thaw_object(
            self.environment_snapshot,
            field_name="review environment_snapshot",
        )


RunCommand: typing.TypeAlias = SubmitTurnCommand | SubmitReviewCommand


def parse_run_command(
    value: Mapping[str, ThawedJsonValue],
) -> RunCommand:
    """按命令判别字段还原本地主动 Run 命令。"""
    if not isinstance(value, Mapping):
        raise TypeError("persisted run command must be an object")
    kind = value.get("kind")
    if kind == "submit_turn":
        return SubmitTurnCommand.from_dict(value)
    if kind == "submit_review":
        return SubmitReviewCommand.from_dict(value)
    raise ValueError("unsupported command kind")


def _command_fingerprint(value: dict[str, ThawedJsonValue]) -> str:
    """计算忽略投递身份的稳定命令意图指纹。"""
    material = dict(value)
    material.pop("command_id")
    material.pop("causation_id")
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_frozen_object(
    value: Mapping[str, JsonValue],
    *,
    field_name: str,
) -> Mapping[str, JsonValue]:
    """校验并冻结一个命令 JSON 对象。"""
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    frozen = freeze_json(dict(value), field_name=field_name)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return frozen


def _optional_frozen_object(
    value: Mapping[str, JsonValue] | None,
    *,
    field_name: str,
) -> Mapping[str, JsonValue] | None:
    """校验并冻结一个可空命令 JSON 对象。"""
    if value is None:
        return None
    return _required_frozen_object(value, field_name=field_name)


def _new_id(prefix: str) -> str:
    """生成带职责前缀的本地随机身份。"""
    return f"{prefix}_{uuid.uuid4().hex}"


if __name__ == '__main__':
    pass
