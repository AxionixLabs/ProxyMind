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
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_http")
    async def nexus_http(
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_http
        P:
          payload: dict  # HTTP 入口（单请求或 items 并发）；支持模板 {{expr}}
            - env?: {base_url?: str, headers?: dict[str,str], timeout?: float}
            - vars?: dict[str,any]                # 初始模板上下文
            - options?: {fail_fast?: bool=True}
            - prepare?: list[dict]                # 常见 prepare type 包括：uuid4 / timestamp_ms / nonce / format / hmac_sha256 / rsa_sign_sha256 / jwt_hs256 等
            - extract?: dict[str,str]             # alias -> path
            - asserts?: list[dict]                # {path, op, value?}
            - request(单请求字段): method/url/base_url/headers/params/json/body/form/files/timeout/retries/follow_redirects/save_response/save_dir
            - items?(批请求): list[{name?, prepare?, request, extract?, asserts?}]
          concurrency: int=1                      # items 并发度（Semaphore）
        R: CTR
        N:
          - 执行模式：items 为空=单请求；items 非空=批请求（每个 item 独立 prepare/extract/asserts）
          - env 合并：request.base_url/timeout 覆盖 env；headers 叠加（request 覆盖 env 同名键）
          - 模板：作用于 env/options/prepare/request/extract/asserts；上下文=vars + 当前 step 变量；受限表达式（无函数/导入/推导式）
          - prepare：顺序执行，仅注入当前 step_ctx；不回写全局 ctx；并发 items 之间 ctx 隔离不串值
          - 证据：data.request / data.response；媒体响应写入 response.media[]；save_response=true 时落盘并补充 path/filename/mime_type/size
        """

        args = {
            "payload"     : payload,
            "concurrency" : concurrency
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.nexus_http", args=args)
            try:
                return await Ins.nexus.nexus_http(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_http",
            args=args,
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_sse")
    async def nexus_sse(
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_sse
        P:
          payload: dict  # SSE 入口（单请求或 items 并发）；支持模板 {{expr}}
            - env?: {base_url?: str, headers?: dict[str,str], timeout?: float}
            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}
            - prepare?: list[dict]           # 常见 prepare type 包括：uuid4 / timestamp_ms / nonce / format / hmac_sha256 / rsa_sign_sha256 / jwt_hs256 等
            - extract?: dict[str,str]
            - asserts?: list[dict]
            - request(SSE 字段): method/url/base_url/headers/params/json/body/form/files/timeout/retries/follow_redirects
              - max_events: int?=None        # None=不设上限；非空达到即提前返回
              - media_index: int?            # 从 events 选第几个事件（默认首个）
              - media_path: str?             # 在目标事件内按路径取媒体字段
              - save_response: bool=False
              - save_dir: str?
            - items?(批请求): list[{name?, prepare?, request, extract?, asserts?}]
          concurrency: int=1
        R: CTR
        N:
          - 执行模式：items 为空=单 SSE；items 非空=批 SSE（每个 item 独立 prepare/extract/asserts）
          - env 合并：request.base_url/timeout 覆盖 env；headers 叠加（request 覆盖 env 同名键）
          - 模板：作用于 env/options/prepare/request/extract/asserts；上下文=vars+step 变量；受限表达式
          - prepare：顺序执行，仅注入当前 step_ctx；并发 items 之间 ctx 隔离不串值
          - events: response.events[]={event,id,data}；max_events 达到提前返回，否则流结束后返回
          - media: 可用 media_index/media_path 从 events 提取媒体写入 response.media[]；save_response=true 时落盘并补充 path/filename/mime_type/size
        """

        args = {
            "payload"     : payload,
            "concurrency" : concurrency
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.nexus_sse", args=args)
            try:
                return await Ins.nexus.nexus_sse(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_sse",
            args=args,
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_ws")
    async def nexus_ws(
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_ws
        P:
          payload: dict  # WebSocket 入口（单请求或 items 并发）；支持模板 {{expr}}
            - env?: {headers?: dict[str,str], timeout?: float}
            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}
            - prepare?: list[dict]          # 常见 prepare type 包括：uuid4 / timestamp_ms / nonce / format / hmac_sha256 / rsa_sign_sha256 / jwt_hs256 等
            - extract?: dict[str,str]
            - asserts?: list[dict]
            - request(WS 字段): url/headers/sends/timeout
              - max_messages: int=10
              - media_index: int?           # 从 messages 选第几个消息（默认首个）
              - media_path: str?            # 在目标消息内按路径取媒体字段（消息会尝试 JSON 解析用于提取）
              - save_response: bool=False
              - save_dir: str?
            - items?(批请求): list[{name?, prepare?, request, extract?, asserts?}]
          concurrency: int=1
        R: CTR
        N:
          - 执行模式：items 为空=单 WS；items 非空=批 WS（每个 item 独立 prepare/extract/asserts）
          - env 合并：request.timeout 覆盖 env；headers 叠加（request 覆盖 env 同名键）
          - 模板：作用于 env/options/prepare/request/extract/asserts；上下文=vars+step 变量；受限表达式
          - prepare：顺序执行，仅注入当前 step_ctx；并发 items 之间 ctx 隔离不串值
          - messages: 最多采集 max_messages 条写入 response.messages[]；ConnectionClosedOK 会提前停止
          - media: media_index/media_path 从消息提取媒体写入 response.media[]；save_response=true 时落盘并补充 path/filename/mime_type/size
        """

        args = {
            "payload"     : payload,
            "concurrency" : concurrency
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.nexus_ws", args=args)
            try:
                return await Ins.nexus.nexus_ws(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_ws",
            args=args,
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_graphql")
    async def nexus_graphql(
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> CallToolResult:
        """
        D: bench
        C: nexus
        A: nexus_graphql
        P:
          payload: dict  # GraphQL 入口（单请求或 items 并发）；支持模板 {{expr}}
            - env?: {base_url?: str, headers?: dict[str,str], timeout?: float}
            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}
            - prepare?: list[dict]  # 常见 prepare type 包括：uuid4 / timestamp_ms / nonce / format / hmac_sha256 / rsa_sign_sha256 / jwt_hs256 等
            - extract?: dict[str,str]
            - asserts?: list[dict]
            - request(GraphQL 字段): url/query/variables/operation_name|operationName/base_url/headers/params/timeout/retries/follow_redirects
              - media_path: str?
              - save_response: bool=False
              - save_dir: str?
            - items?(批请求): list[{name?, prepare?, request, extract?, asserts?}]
          concurrency: int=1
        R: CTR
        N:
          - 执行模式：items 为空=单 GraphQL；items 非空=批 GraphQL（每个 item 独立 prepare/extract/asserts）
          - env 合并：request.base_url/timeout 覆盖 env；headers 叠加（request 覆盖 env 同名键）
          - 模板：作用于 env/options/prepare/request/extract/asserts；上下文=vars+step 变量；受限表达式
          - prepare：顺序执行，仅注入当前 step_ctx；并发 items 之间 ctx 隔离不串值
          - 请求：HTTP POST {query, variables, operationName}；2xx 但 errors 非空仍视为失败（证据写入 data.graphql）
          - media: media_path 从 body_json 提取媒体写入 response.media[]；save_response=true 时落盘并补充 path/filename/mime_type/size
        """

        args = {
            "payload"     : payload,
            "concurrency" : concurrency
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.nexus_graphql", args=args)
            try:
                return await Ins.nexus.nexus_graphql(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_graphql",
            args=args,
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
