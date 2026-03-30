# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import BaseModel
from backend.models.model_nexus import (
    NexusBatchItem,
    NexusBatchRequest,
    NexusKind,
    NexusRequest
)


def request_model(
    *,
    request: typing.Any,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    extract: typing.Optional[dict[str, str]] = None,
    asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
    name: typing.Optional[str] = None
) -> NexusRequest:
    """根据 MCP 工具参数构建标准化的单请求模型。"""
    request_data = dump_model(request, field_name="request")
    return NexusRequest(
        name=name,
        request=request_data,
        template_vars=dict(template_vars or {}),
        extract=extract,
        asserts=asserts
    )


def dump_model(value: typing.Any, *, field_name: str = "value") -> dict[str, typing.Any]:
    """将嵌套的 Pydantic/dict 负载标准化为普通字典。"""
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True, by_alias=True)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"{field_name} must be a dict or Pydantic model, got {type(value).__name__}")


def batch_model(
    *,
    items: list[typing.Any],
    env: typing.Optional[typing.Any] = None,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    concurrency: int = 1,
    fail_fast: bool = True
) -> NexusBatchRequest:
    """根据 MCP 工具参数构建标准化的批量请求模型。"""
    normalized_items: list[NexusBatchItem] = []

    for item in items:
        item_dict = dump_model(item, field_name="items[]")
        if not item_dict:
            continue
        normalized_items.append(
            NexusBatchItem(
                name=item_dict.get("name"),
                request=dump_model(item_dict.get("request"), field_name="items[].request"),
                extract=item_dict.get("extract") if isinstance(item_dict.get("extract"), dict) else None,
                asserts=item_dict.get("asserts") if isinstance(item_dict.get("asserts"), list) else None
            )
        )

    return NexusBatchRequest(
        items=normalized_items,
        env=dump_model(env, field_name="env"),
        template_vars=dict(template_vars or {}),
        concurrency=concurrency,
        fail_fast=fail_fast
    )


def flat_batch_model(
    *,
    items: list[typing.Any],
    env: typing.Optional[typing.Any] = None,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    concurrency: int = 1,
    fail_fast: bool = True
) -> NexusBatchRequest:
    """根据扁平条目负载构建标准化的批量请求模型。"""
    normalized_items: list[NexusBatchItem] = []

    for item in items:
        item_dict = dump_model(item, field_name="items[]")
        if not item_dict:
            continue
        normalized_items.append(
            NexusBatchItem(
                name=item_dict.get("name"),
                request={
                    key: value
                    for key, value in item_dict.items()
                    if key not in {"name", "extract", "asserts"}
                },
                extract=item_dict.get("extract") if isinstance(item_dict.get("extract"), dict) else None,
                asserts=item_dict.get("asserts") if isinstance(item_dict.get("asserts"), list) else None,
            )
        )

    return NexusBatchRequest(
        items=normalized_items,
        env=dump_model(env, field_name="env"),
        template_vars=dict(template_vars or {}),
        concurrency=concurrency,
        fail_fast=fail_fast
    )


def batch_args_payload(
    batch_args: BaseModel,
    *,
    kind: typing.Optional[NexusKind] = None
) -> dict[str, typing.Any]:
    """将已校验的批量参数标准化，用于日志和广播负载。"""
    args = batch_args.model_dump(exclude_none=True, by_alias=True)
    if kind is not None:
        args = {"kind": kind, **args}
    return args


def flat_batch_args_payload(
    *,
    items: list[typing.Any],
    env: typing.Optional[typing.Any] = None,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    concurrency: int = 1,
    fail_fast: bool = True
) -> dict[str, typing.Any]:
    """将扁平批量工具参数标准化，用于日志和广播负载。"""
    return {
        "items"         : [dump_model(item, field_name="items[]") for item in items],
        "env"           : dump_model(env, field_name="env") if env is not None else None,
        "template_vars" : dict(template_vars or {}) if template_vars is not None else None,
        "concurrency"   : concurrency,
        "fail_fast"     : fail_fast
    }


def generic_batch_args_payload(
    *,
    kind: NexusKind,
    items: list[typing.Any],
    env: typing.Optional[typing.Any] = None,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    concurrency: int = 1,
    fail_fast: bool = True
) -> dict[str, typing.Any]:
    """将通用批量工具参数标准化，用于日志和广播负载。"""
    return {
        "kind"          : kind,
        "items"         : [dump_model(item, field_name="items[]") for item in items],
        "env"           : dump_model(env, field_name="env") if env is not None else None,
        "template_vars" : dict(template_vars or {}) if template_vars is not None else None,
        "concurrency"   : concurrency,
        "fail_fast"     : fail_fast
    }


if __name__ == '__main__':
    pass
