# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from ..result import client_tool_result
from ..types import (
    ClientTool, ClientToolRuntime
)

SAMPLE_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "value": {
            "type": "string",
            "description": "客户端示例工具回显的示例值。",
        },
    },
    "additionalProperties": True,
}


async def sample_handler(
    arguments: dict[str, typing.Any],
    runtime: ClientToolRuntime
):
    """客户端示例处理函数。"""
    _ = runtime
    return client_tool_result(
        tool="client_example",
        ok=True,
        text="client example completed",
        args=arguments,
        data={
            "received": dict(arguments or {}),
        },
    )


SAMPLE_TOOL = ClientTool(
    name="client_example",
    description=(
        "客户端示例工具，用于展示注册表约定，不绑定具体业务流程。"
    ),
    input_schema=SAMPLE_INPUT_SCHEMA,
    meta={
        "domain": "example",
        "class": "client_tool",
        "hidden": True,
    },
    handler=sample_handler,
)


if __name__ == '__main__':
    pass
