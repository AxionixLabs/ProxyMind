# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.adapters.nexus_adapter import (
    request_model,
    dump_model,
    flat_batch_model,
    flat_batch_args_payload,
    generic_batch_args_payload,
)
from backend.mcp_tools.bench.schemas.nexus_schema import (
    NexusKindArg,
    NexusRequestArg,
    NexusEnvArg,
    NexusTemplateVarsArg,
    NexusExtractArg,
    NexusAssertsArg,
    NexusNameArg,
    GenericBatchItemsArg,
    GenericBatchEnvArg,
    HttpBatchItemsArg,
    HttpBatchEnvArg,
    SseBatchItemsArg,
    SseBatchEnvArg,
    GraphqlBatchItemsArg,
    GraphqlBatchEnvArg,
    WsBatchItemsArg,
    WsBatchEnvArg,
    TcpBatchItemsArg,
    TcpBatchEnvArg,
    UdpBatchItemsArg,
    UdpBatchEnvArg,
    SmtpBatchItemsArg,
    SmtpBatchEnvArg,
    ImapBatchItemsArg,
    ImapBatchEnvArg,
    FtpBatchItemsArg,
    FtpBatchEnvArg,
    HttpRequestArg,
    SseRequestArg,
    GraphqlRequestArg,
    WsRequestArg,
    TcpRequestArg,
    UdpRequestArg,
    SmtpRequestArg,
    ImapRequestArg,
    FtpRequestArg,
    NexusConcurrencyArg,
    NexusFailFastArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


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
                    request=request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    ),
                    env=dump_model(env)
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
                    request=request_model(
                        request=request,
                        template_vars=template_vars,
                        extract=extract,
                        asserts=asserts,
                        name=name
                    ),
                    env=dump_model(env)
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
            "渲染批量请求中的共享默认值和各项模板变量，并展示物化后的最终请求。"
            "该工具只返回渲染结果，不执行协议请求。"
            "`env` 作为批量共享默认值，`items[]` 直接包含协议字段；执行前会先把两者物化成最终请求。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_render_batch")
    async def nexus_render_batch(
        kind: NexusKindArg,
        items: GenericBatchItemsArg,
        env: GenericBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = generic_batch_args_payload(
            kind=kind,
            items=items,
            env=env,
            template_vars=template_vars,
            concurrency=concurrency,
            fail_fast=fail_fast
        )

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.render_batch", args=args)
            try:
                return ctx.nexus.render_batch(
                    kind=kind,
                    batch=flat_batch_model(
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
            "适合在批跑前先检查 flat `items` 结构、共享 `env` 和并发参数是否合理。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_validate_batch")
    async def nexus_validate_batch(
        kind: NexusKindArg,
        items: GenericBatchItemsArg,
        env: GenericBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = generic_batch_args_payload(
            kind=kind,
            items=items,
            env=env,
            template_vars=template_vars,
            concurrency=concurrency,
            fail_fast=fail_fast
        )

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.validate_batch", args=args)
            try:
                return ctx.nexus.validate_batch(
                    kind=kind,
                    batch=flat_batch_model(
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
        request: HttpRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast；一旦某项失败是否立即停止，取决于 `fail_fast`。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_http_batch")
    async def nexus_http_batch(
        items: HttpBatchItemsArg,
        env: HttpBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(
            items=items,
            env=env,
            template_vars=template_vars,
            concurrency=concurrency,
            fail_fast=fail_fast
        )

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="http",
                    batch=flat_batch_model(
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
        request: SseRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条流式用例的统一回放。"
            "若预期非默认行为，必须显式传入 `concurrency` 和 `fail_fast`，不要通过省略字段回退到默认 `1/true`。"
            "`env` 与 `items` 必须传结构化对象，不要传字符串化 JSON。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_sse_batch")
    async def nexus_sse_batch(
        items: SseBatchItemsArg,
        env: SseBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="sse",
                    batch=flat_batch_model(
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
        request: WsRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条 WebSocket 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ws_batch")
    async def nexus_ws_batch(
        items: WsBatchItemsArg,
        env: WsBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="ws",
                    batch=flat_batch_model(
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
        request: GraphqlRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条 GraphQL 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_graphql_batch")
    async def nexus_graphql_batch(
        items: GraphqlBatchItemsArg,
        env: GraphqlBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="graphql",
                    batch=flat_batch_model(
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
        request: TcpRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条端口探测或原始报文用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_tcp_batch")
    async def nexus_tcp_batch(
        items: TcpBatchItemsArg,
        env: TcpBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="tcp",
                    batch=flat_batch_model(
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
        request: UdpRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条 UDP 探测用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_udp_batch")
    async def nexus_udp_batch(
        items: UdpBatchItemsArg,
        env: UdpBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="udp",
                    batch=flat_batch_model(
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
        request: SmtpRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条 SMTP 校验或发信用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_smtp_batch")
    async def nexus_smtp_batch(
        items: SmtpBatchItemsArg,
        env: SmtpBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="smtp",
                    batch=flat_batch_model(
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
        request: ImapRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条邮箱用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_imap_batch")
    async def nexus_imap_batch(
        items: ImapBatchItemsArg,
        env: ImapBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="imap",
                    batch=flat_batch_model(
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
        request: FtpRequestArg,
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
                    request=request_model(
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
            "`env` 提供共享默认值，`items[]` 提供逐项差异；执行前会先物化成最终请求。"
            "支持并发执行与 fail-fast，适合多条 FTP 用例的统一回放。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "nexus"}
    )
    @task_middleware("nexus_ftp_batch")
    async def nexus_ftp_batch(
        items: FtpBatchItemsArg,
        env: FtpBatchEnvArg = None,
        template_vars: NexusTemplateVarsArg = None,
        concurrency: NexusConcurrencyArg = 1,
        fail_fast: NexusFailFastArg = True
    ) -> CallToolResult:
        args = flat_batch_args_payload(items=items, env=env, template_vars=template_vars, concurrency=concurrency, fail_fast=fail_fast)

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{ctx.nexus.agent_id}.execute_batch", args=args)
            try:
                return await ctx.nexus.execute_batch(
                    kind="ftp",
                    batch=flat_batch_model(
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
