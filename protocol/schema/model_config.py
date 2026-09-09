# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from protocol.schema.json_value import JsonValue

MODEL_CONTEXT_FIELDS = frozenset({
    "model_context_window",
    "model_auto_compact_token_limit",
})


class ModelContextConfig(typing.TypedDict, total=False):
    """声明模型配置中的可选 token 策略；解析后只包含有效的整数值。"""

    model_context_window: int
    model_auto_compact_token_limit: int


def parse_model_context_config(payload: Mapping[str, JsonValue]) -> ModelContextConfig:
    """校验可选模型窗口与触发阈值，空值表示使用服务端模型配置。"""
    window = payload.get("model_context_window")
    limit = payload.get("model_auto_compact_token_limit")
    result: ModelContextConfig = {}
    if window is not None:
        if isinstance(window, bool) or not isinstance(window, int) or window <= 1:
            raise ValueError("model_context_window must be an integer greater than 1")
        result["model_context_window"] = window
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("model_auto_compact_token_limit must be a positive integer")
        if isinstance(window, int) and limit >= window:
            raise ValueError("model_auto_compact_token_limit must be smaller than model_context_window")
        result["model_auto_compact_token_limit"] = limit
    return result


if __name__ == '__main__':
    pass
