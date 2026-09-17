# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import datetime
import math
import re
import typing
from dataclasses import dataclass
from urllib.parse import urlsplit

FormValue: typing.TypeAlias = str | int | float | bool | tuple[str, ...]
ElicitationAction: typing.TypeAlias = typing.Literal["accept", "decline", "cancel"]
FieldKind: typing.TypeAlias = typing.Literal["string", "integer", "number", "boolean", "array"]


@dataclass(frozen=True, slots=True)
class McpInvocation:
    """绑定本地调用身份和冻结交互策略；不生成远端 Effect 或工具审批事实。"""

    session_id: str
    turn_id: str
    call_id: str
    agent_id: str
    allow_elicitation: bool = True

    def __post_init__(self) -> None:
        """拒绝不能定位到具体调用的交互来源。"""
        if not all(value.strip() for value in (self.session_id, self.turn_id, self.call_id, self.agent_id)):
            raise ValueError("MCP invocation identity is required")


@dataclass(frozen=True, slots=True)
class ElicitationField:
    """保存边界已验证的扁平字段；字段值只在当前展示和应答范围内存活。"""

    name: str
    title: str
    description: str
    kind: FieldKind
    required: bool
    choices: tuple[tuple[str, str], ...] = ()
    default: FormValue | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int = 0
    max_length: int = 4096
    min_items: int = 0
    max_items: int = 64
    format: str | None = None

    def validate(self, value: FormValue) -> None:
        """验证用户输入的真实类型和已声明约束，错误不包含输入正文。"""
        valid = False
        if self.kind == "boolean":
            valid = isinstance(value, bool)
        elif self.kind in ("integer", "number"):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                valid = finite and (self.kind != "integer" or isinstance(value, int))
                valid = valid and (self.minimum is None or value >= self.minimum) and (self.maximum is None or value <= self.maximum)
        elif self.kind == "string" and isinstance(value, str):
            valid = self.min_length <= len(value) <= self.max_length
            valid = valid and (not self.choices or value in dict(self.choices))
            if valid and self.format is not None:
                valid = _valid_format(value, self.format)
        elif self.kind == "array" and isinstance(value, tuple):
            valid = self.min_items <= len(value) <= self.max_items and len(set(value)) == len(value)
            valid = valid and all(item in dict(self.choices) for item in value)
        if not valid:
            raise ValueError("Value does not match the requested field constraints")


def _valid_format(value: str, format_name: str) -> bool:
    """对支持的普通信息格式执行有界验证，不访问网络或加载外部 schema。"""
    try:
        if format_name == "email":
            return re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is not None
        if format_name == "uri":
            return bool(urlsplit(value).scheme) and not any(character.isspace() for character in value)
        if format_name == "date":
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and bool(datetime.date.fromisoformat(value))
        if format_name == "date-time":
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "T" in value and parsed.tzinfo is not None
    except ValueError:
        return False
    return False


@dataclass(frozen=True, slots=True)
class ElicitationRequest:
    """描述一次本地 MCP 交互；身份包含连接生成的唯一 ID 和来源调用，不进入线上审批协议。"""

    request_id: str
    server: str
    invocation: McpInvocation
    message: str
    fields: tuple[ElicitationField, ...] | None = None
    url: str | None = None
    url_host: str | None = None
    elicitation_id: str | None = None

    def validate_response(self, response: "ElicitationResponse") -> None:
        """在发送前再次核对结果，拒绝未知字段、重复字段及非 accept 的数据。"""
        if response.action != "accept" or self.fields is None:
            if response.content:
                raise ValueError("This elicitation response cannot contain form data")
            return
        values = dict(response.content)
        if len(values) != len(response.content) or values.keys() - {field.name for field in self.fields}:
            raise ValueError("Unknown or duplicate elicitation field")
        for field in self.fields:
            if field.name not in values:
                if field.required:
                    raise ValueError("A required field is missing")
            else:
                field.validate(values[field.name])


@dataclass(frozen=True, slots=True)
class ElicitationResponse:
    """保存一次交互终态；正文不进入审批事实、grant、日志或会话历史。"""

    action: ElicitationAction
    content: tuple[tuple[str, FormValue], ...] = ()


if __name__ == '__main__':
    pass
