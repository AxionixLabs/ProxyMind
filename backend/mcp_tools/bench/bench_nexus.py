#  ____                  _       _   _
# | __ )  ___ _ __   ___| |__   | \ | | _____  ___   _ ___
# |  _ \ / _ \ '_ \ / __| '_ \  |  \| |/ _ \ \/ / | | / __|
# | |_) |  __/ | | | (__| | | | | |\  |  __/>  <| |_| \__ \
# |____/ \___|_| |_|\___|_| |_| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.nexus.domain.model import (
    NexusBatchItem, NexusBatchRequest, NexusKind, NexusRequest
)
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


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


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_render_request")
    async def nexus_render_request(
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_render_request
        P:
          kind: NexusKind
          request: dict
          env: dict?=None
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - 单请求统一使用 `request` 边界
          - 仅做模板渲染与默认值合并，不执行网络调用
        """
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
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.render_request", args=args)
            try:
                return Ins.nexus.render_request(
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
            target_list=[Ins.nexus],
            call=call, overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_validate_request")
    async def nexus_validate_request(
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_validate_request
        P:
          kind: NexusKind
          request: dict
          env: dict?=None
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - 单请求统一使用 `request` 边界
          - 仅做结构校验与模板渲染，不执行网络调用
        """
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
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.validate_request", args=args)
            try:
                return Ins.nexus.validate_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_render_batch")
    async def nexus_render_batch(
        kind: NexusKind,
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_render_batch
        P:
          kind: NexusKind
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - 批量统一使用 `items + env` 边界
          - 仅做模板渲染与默认值合并，不执行网络调用
        """
        args = {
            "kind"          : kind,
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.render_batch", args=args)
            try:
                return Ins.nexus.render_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_validate_batch")
    async def nexus_validate_batch(
        kind: NexusKind,
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_validate_batch
        P:
          kind: NexusKind
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - 批量统一使用 `items + env` 边界
          - 仅做结构校验与模板渲染，不执行网络调用
        """
        args = {
            "kind"          : kind,
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.validate_batch", args=args)
            try:
                return Ins.nexus.validate_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_http_request")
    async def nexus_http_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_http_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - HTTP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `url` 必填；
            `method/headers/params/json/body/body_text/form/files/timeout/retries/follow_redirects` 可选；
            `artifact_dir` 可选，传入后会把 request/response/extract/media 落到 `<artifact_dir>/nexus/artifacts/...`
          - `extract` / `asserts` 作用于最终 `pack.data`，常见路径如 `response.status`、`response.body_json`、`response.media.0.path`
          - 适合单次接口调用、提取和断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_http_batch")
    async def nexus_http_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_http_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - HTTP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_http_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_sse_request")
    async def nexus_sse_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_sse_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - SSE 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `url` 必填；
            `method/headers/params/json/body/body_text/form/files/timeout/retries/follow_redirects/max_events` 可选；
            `media_index/media_path` 用于从事件流中定位媒体引用；
            `artifact_dir` 可选，传入后会把事件证据链和媒体文件一起落盘
          - `extract` / `asserts` 作用于最终 `pack.data`，常见路径如 `response.events.0.data`、`response.media.0.path`
          - 适合流式事件消费、提取和断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_sse_batch")
    async def nexus_sse_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_sse_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - SSE 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_sse_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_ws_request")
    async def nexus_ws_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_ws_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - WebSocket 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `url` 必填；
            `headers/sends/timeout/max_messages` 可选；
            `media_index/media_path` 用于从消息列表中定位媒体引用；
            `artifact_dir` 可选，传入后会把消息证据链和媒体文件一起落盘
          - `extract` / `asserts` 作用于最终 `pack.data`，常见路径如 `response.messages.0`、`response.media.0.path`
          - 适合建连、发送、接收和断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_ws_batch")
    async def nexus_ws_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_ws_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - WebSocket 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_ws_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_graphql_request")
    async def nexus_graphql_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_graphql_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - GraphQL 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `url/query` 必填；
            `variables/operation_name/headers/params/timeout/retries/follow_redirects` 可选；
            `media_path` 用于从 GraphQL JSON 结果中定位媒体引用；
            `artifact_dir` 可选，传入后会把 response/extract/media 一起落盘
          - `extract` / `asserts` 作用于最终 `pack.data`，常见路径如 `response.body_json.data`、`response.media.0.path`
          - 适合 query / mutation 的单次调用
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_graphql_batch")
    async def nexus_graphql_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_graphql_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - GraphQL 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_graphql_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_tcp_request")
    async def nexus_tcp_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_tcp_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - TCP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `host/port` 必填；
            `body_text/sends/encoding/timeout/read_size/close_write/max_reads/read_until` 可选；
          - `response` 为 TCP 协议字段模板，常见路径有：
            `response.body_text`、`response.messages`、`response.remote.host`、`response.body_hex`
          - `extract` / `asserts` 作用于最终 `pack.data`
          - 适合端口连通、原始报文发送和响应断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_tcp_batch")
    async def nexus_tcp_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_tcp_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - TCP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_tcp_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_udp_request")
    async def nexus_udp_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_udp_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - UDP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `host/port/body_text` 必填；
            `encoding/timeout/read_size` 可选；
          - `response` 为 UDP 协议字段模板，常见路径有：
            `response.body_text`、`response.remote.host`、`response.body_hex`
          - `extract` / `asserts` 作用于最终 `pack.data`
          - 适合轻量探测、报文发送和响应断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_udp_batch")
    async def nexus_udp_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_udp_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - UDP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_udp_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_smtp_request")
    async def nexus_smtp_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_smtp_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - SMTP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `host/port` 必填；
            `action` 可选，支持 `noop/send`；
            发送邮件时常用 `username/password/use_ssl/use_tls/from_addr/to_addrs/subject/body_text/html_body/attachments/timeout`
          - `response` 为 SMTP 协议字段模板，常见路径有：
            `response.action`、`response.ehlo.code`、`response.noop.code`、`response.send.accepted`、`response.attachments`
          - `extract` / `asserts` 作用于最终 `pack.data`
          - 适合连通性验证、NOOP 和发送邮件测试
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_smtp_batch")
    async def nexus_smtp_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_smtp_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - SMTP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_smtp_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_imap_request")
    async def nexus_imap_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_imap_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - IMAP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `host/port/username/password` 必填；
            `action/mailbox/criteria/message_set/fetch_parts/parse_messages/use_ssl/timeout` 可选；
            `media_path` 可选，用于从 `parsed_messages` 或附件结构中定位媒体引用；
            `artifact_dir` 可选，传入后会把 IMAP 结果、提取结果和命中的媒体文件一起落盘
          - `response` 为 IMAP 协议字段模板，常见路径有：
            `response.login.type`、`response.select.type`、`response.fetch.items`、`response.parsed_messages.0.subject`、`response.media.0.path`
          - `extract` / `asserts` 作用于最终 `pack.data`
          - 适合邮箱登录、搜索、抓取和断言
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_imap_batch")
    async def nexus_imap_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_imap_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - IMAP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_imap_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_ftp_request")
    async def nexus_ftp_request(
        request: dict[str, typing.Any],
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        name: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_ftp_request
        P:
          request: dict
          template_vars: dict?=None
          extract: dict?=None
          asserts: list[dict]?=None
          name: str?=None
        R: CTR
        N:
          - FTP 单请求工具
          - `request` 直接传协议字段，不再平铺为工具参数
          - `request` 常用字段：
            `host/port` 必填；
            `action` 可选，常见值有 `list/upload_text/upload_binary/download_text/download_binary/delete/mkdir`；
            `username/password/path/payload_text/payload_base64/encoding/use_tls/timeout` 可选；
            `media_path` 可选，用于从下载结果中定位媒体引用；
            `artifact_dir` 可选，传入后会把 FTP 结果、提取结果和命中的媒体文件一起落盘
          - `response` 为 FTP 协议字段模板，常见路径有：
            `response.welcome`、`response.list`、`response.download_binary.filename`、`response.upload_text.reply`、`response.media.0.path`
          - `extract` / `asserts` 作用于最终 `pack.data`
          - 适合列目录、上传、下载和删除测试
        """
        args = {
            "request"       : request,
            "template_vars" : template_vars,
            "extract"       : extract,
            "asserts"       : asserts,
            "name"          : name
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_request", args=args)
            try:
                return await Ins.nexus.execute_request(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_ftp_batch")
    async def nexus_ftp_batch(
        items: list[dict[str, typing.Any]],
        env: typing.Optional[dict[str, typing.Any]] = None,
        template_vars: typing.Optional[dict[str, typing.Any]] = None,
        concurrency: int = 1,
        fail_fast: bool = True
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_ftp_batch
        P:
          items: list[dict]
          env: dict?=None
          template_vars: dict?=None
          concurrency: int=1
          fail_fast: bool=True
        R: CTR
        N:
          - FTP 批量请求工具
          - `env` 提供共享默认值，`items[].request` 覆盖同名字段
          - `items[].request` 结构与 `nexus_ftp_request.request` 相同
          - 支持并发执行和 fail-fast
        """
        args = {
            "items"         : items,
            "env"           : env,
            "template_vars" : template_vars,
            "concurrency"   : concurrency,
            "fail_fast"     : fail_fast
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.execute_batch", args=args)
            try:
                return await Ins.nexus.execute_batch(
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
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
