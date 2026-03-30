# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.middlewares.mid_task import task_middleware
from backend.models.model_nexus import (
    NexusBatchItem,
    NexusBatchRequest,
    NexusKind,
    NexusRequest
)
from backend.utilities.runtime import AppContext, Idle
from backend.utilities.broadcast import broadcast


NexusKindArg = typing.Annotated[
    NexusKind,
    Field(description="Nexus 协议类型，如 http、sse、ws、graphql、tcp、udp、smtp、imap 或 ftp。"),
]
NexusRequestArg = typing.Annotated[
    dict[str, typing.Any],
    Field(description="单次请求定义。协议相关字段都放在这里，例如 url、method、headers、body、messages 或 action。"),
]
NexusEnvArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="批量或预执行阶段的共享默认值。执行或校验时会先应用这里的字段，再由当前 `request` 覆盖同名字段。"),
]
NexusTemplateVarsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="模板变量字典，用于渲染请求中的占位符。"),
]
NexusExtractArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="结果提取规则，键为输出名，值为提取表达式或路径。"),
]
NexusAssertsArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="断言列表。每项通常描述比较目标、操作符和期望值。"),
]
NexusNameArg = typing.Annotated[
    typing.Optional[str],
    Field(description="可选用例名，便于在结果、日志和批量输出中识别当前请求。"),
]
NexusBatchItemsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(description="批量请求项列表。每项可包含 `name`、`request`、`extract` 和 `asserts`。"),
]
NexusConcurrencyArg = typing.Annotated[
    int,
    Field(description="批量执行的最大并发数。"),
]
NexusFailFastArg = typing.Annotated[
    bool,
    Field(description="批量执行时遇到首个失败是否立即停止剩余请求。"),
]


def _request_model(
    *,
    request: dict[str, typing.Any],
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    extract: typing.Optional[dict[str, str]] = None,
    asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
    name: typing.Optional[str] = None
) -> NexusRequest:
    """Build normalized single-request model from MCP tool arguments."""
    return NexusRequest(
        name=name,
        request=dict(request or {}),
        template_vars=dict(template_vars or {}),
        extract=extract,
        asserts=asserts
    )


def _batch_model(
    *,
    items: list[dict[str, typing.Any]],
    env: typing.Optional[dict[str, typing.Any]] = None,
    template_vars: typing.Optional[dict[str, typing.Any]] = None,
    concurrency: int = 1,
    fail_fast: bool = True,
) -> NexusBatchRequest:
    """Build normalized batch model from MCP tool arguments."""
    return NexusBatchRequest(
        items=[
            NexusBatchItem(
                name=item.get("name"),
                request=dict(item.get("request") or {}),
                extract=item.get("extract") if isinstance(item.get("extract"), dict) else None,
                asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else None,
            )
            for item in items
            if isinstance(item, dict)
        ],
        env=dict(env or {}),
        template_vars=dict(template_vars or {}),
        concurrency=concurrency,
        fail_fast=fail_fast
    )


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "渲染单个标准化请求的模板变量和共享默认值。"
            "该工具只返回渲染结果，不执行协议请求，也不做联机探测。"
            "适合在真正执行前确认模板展开后的请求形态。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_render_request")
    async def nexus_render_request(
        kind: NexusKindArg,
        request: NexusRequestArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "kind"          : kind,
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name,
            "env"           : env
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.render_request", args=args)
            try:
                return ctx.nexus.render_request(
                    kind=kind,
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    ),
                    env=dict(env or {})
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_render_request",
            args=args,
            target_list=[ctx.nexus],
            call=call, overrides=None
        )

    @mcp.tool(
        description=(
            "校验单个标准化请求的基础结构，并返回渲染后的结果。"
            "该工具只做字段校验和模板渲染，不执行协议请求。"
            "适合在批跑前先检查必填字段、协议边界和模板展开后的输入。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_validate_request")
    async def nexus_validate_request(
        kind: NexusKindArg,
        request: NexusRequestArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "kind"          : kind,
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name,
            "env"           : env
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.validate_request", args=args)
            try:
                return ctx.nexus.validate_request(
                    kind=kind,
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    ),
                    env=dict(env or {})
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_validate_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "渲染批量请求中的共享默认值和各项模板变量。"
            "该工具只返回渲染结果，不执行协议请求。"
            "`env` 作为批量共享默认值，`items[].request` 会在执行阶段覆盖同名字段。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_render_batch")
    async def nexus_render_batch(
        kind: NexusKindArg,
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "kind"          : kind,
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.render_batch", args=args)
            try:
                return ctx.nexus.render_batch(
                    kind=kind,
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_render_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "校验批量请求的基础结构，并返回渲染后的批量结果。"
            "该工具只做字段校验和模板渲染，不执行协议请求。"
            "适合在批跑前先检查 `items` 结构、共享 `env` 和并发参数是否合理。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_validate_batch")
    async def nexus_validate_batch(
        kind: NexusKindArg,
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "kind"          : kind,
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.validate_batch", args=args)
            try:
                return ctx.nexus.validate_batch(
                    kind=kind,
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_validate_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 HTTP 请求，并返回标准化结果。"
            "输入边界固定为 `request`，不会把协议字段展开成工具参数。"
            "适合单次接口调用、结果提取和断言；若只想看模板展开结果，应改用 render 或 validate。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_http_request")
    async def nexus_http_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="http",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_http_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 HTTP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast；一旦某项失败是否立即停止，取决于 `fail_fast`。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_http_batch")
    async def nexus_http_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="http",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_http_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 SSE 请求，并消费返回的事件流。"
            "输入边界固定为 `request`，事件证据、媒体命中和提取结果都会归一到标准返回结构中。"
            "适合流式事件消费、提取和断言；若只想看模板展开结果，应改用 render 或 validate。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_sse_request")
    async def nexus_sse_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="sse",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_sse_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 SSE 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条流式用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_sse_batch")
    async def nexus_sse_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="sse",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_sse_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 WebSocket 会话，包括建连、发送和接收。"
            "输入边界固定为 `request`，消息列表、媒体命中和断言结果都会归一到标准返回结构中。"
            "适合一次性的建连验证、消息发送接收和结果断言。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ws_request")
    async def nexus_ws_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="ws",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_ws_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 WebSocket 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条 WebSocket 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ws_batch")
    async def nexus_ws_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="ws",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_ws_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 GraphQL 请求。"
            "输入边界固定为 `request`，其中 `url` 和 `query` 是最核心的请求要素。"
            "适合 query 或 mutation 的单次调用、提取和断言。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_graphql_request")
    async def nexus_graphql_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="graphql",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_graphql_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 GraphQL 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条 GraphQL 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_graphql_batch")
    async def nexus_graphql_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="graphql",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_graphql_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 TCP 连接与报文交互。"
            "输入边界固定为 `request`，适合原始报文发送、读取和响应断言。"
            "该工具面向低层 TCP 校验，不负责高级应用协议语义解析。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_tcp_request")
    async def nexus_tcp_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="tcp",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_tcp_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 TCP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条端口探测或原始报文用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_tcp_batch")
    async def nexus_tcp_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="tcp",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_tcp_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 UDP 报文发送与响应读取。"
            "输入边界固定为 `request`，适合轻量探测、报文发送和响应断言。"
            "UDP 本身不保证可靠送达；超时或无响应需要由调用方按用例判断。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_udp_request")
    async def nexus_udp_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="udp",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_udp_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 UDP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条 UDP 探测用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_udp_batch")
    async def nexus_udp_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="udp",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_udp_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 SMTP 操作。"
            "输入边界固定为 `request`，常见场景是连通性验证、NOOP 或发送测试邮件。"
            "是否真的成功投递邮件，取决于目标 SMTP 服务、认证配置和服务端策略。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_smtp_request")
    async def nexus_smtp_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="smtp",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_smtp_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 SMTP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条 SMTP 校验或发信用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_smtp_batch")
    async def nexus_smtp_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="smtp",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_smtp_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 IMAP 操作。"
            "输入边界固定为 `request`，适合邮箱登录、检索、抓取和结果断言。"
            "若需要媒体命中或附件落盘，应在请求中显式提供对应解析路径或 artifact 目录。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_imap_request")
    async def nexus_imap_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="imap",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_imap_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 IMAP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条邮箱用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_imap_batch")
    async def nexus_imap_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="imap",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_imap_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次 FTP 操作。"
            "输入边界固定为 `request`，常见场景是列目录、上传、下载、删除或建目录。"
            "具体执行哪种动作由 `request.action` 决定；缺少必要字段时会在校验或执行阶段失败。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ftp_request")
    async def nexus_ftp_request(
        request: NexusRequestArg,
        template_vars: NexusTemplateVarsArg = None,
        extract: NexusExtractArg = None,
        asserts: NexusAssertsArg = None,
        name: NexusNameArg = None
    ) -> CallToolResult:
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_request", args=args)
            try:
                return await ctx.nexus.execute_request(
                    kind="ftp",
                    request=_request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_ftp_request",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "批量执行 FTP 请求。"
            "`env` 提供共享默认值，`items[].request` 按项覆盖同名字段。"
            "支持并发执行与 fail-fast，适合多条 FTP 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ftp_batch")
    async def nexus_ftp_batch(
        items: NexusBatchItemsArg,
        env: NexusEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="ftp",
                    batch=_batch_model(
                        items=items,
                        env=env,
                        template_vars=template_vars,
                        concurrency=concurrency,
                        fail_fast=fail_fast
                    )
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_ftp_batch",
            args=args,
            target_list=[ctx.nexus],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
