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
          payload: dict  # HTTP 请求入口（支持单请求或 items 并发）
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]        # {{k}} 模板变量
            - options?: {fail_fast?: bool=True}

            - 单请求（直接顶层字段）:
              - method: str="GET"
              - url: str
              - base_url?: str                # 覆盖 env.base_url
              - headers?: dict[str,str]       # 叠加 env.headers
              - params?: dict[str,any]
              - json?: dict[str,any]
              - json_body?: dict[str,any]     # alias
              - body?: str
              - body_text?: str               # alias
              - form?: dict[str,any]          # application/x-www-form-urlencoded / multipart fields
              - files?: list[dict]            # multipart 文件列表
                - field: str
                - path?: str
                - filename?: str
                - content_type?: str
                - text?: str
                - bytes?: bytes|str
              - timeout?: float               # 覆盖 env.timeout
              - retries?: int
              - follow_redirects?: bool=True
              - extract?: dict[str,str]       # 例如 {"code":"response.body_json.code"}
              - asserts?: list[dict]
                - path: str
                - op: str                     # eq/ne/gt/ge/lt/le/contains/in/exists/empty/not_empty/regex
                - value?: any

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict          # 同“单请求字段”（不含 extract/asserts）
                - item.extract?: dict[str,str]
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1  # items 并发度（Semaphore）
        R: CTR
        N:
          - 支持响应字段提取与断言校验，结果写入 data.extract / data.asserts / data.assert_summary
          - 单请求：payload 顶层字段生效
          - 批请求：payload.items 生效；fail_fast 由 options.fail_fast 控制
        """

        args = {"payload": payload, "concurrency": concurrency}

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
          payload: dict  # SSE 拉流入口（支持单请求或 items 并发）
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}

            - 单请求（直接顶层字段）:
              - url: str
              - base_url?: str
              - headers?: dict[str,str]
              - params?: dict[str,any]
              - timeout?: float
              - max_events?: int=10
              - extract?: dict[str,str]       # 例如 {"first":"events.0.data"}
              - asserts?: list[dict]
                - path: str
                - op: str                     # eq/ne/gt/ge/lt/le/contains/in/exists/empty/not_empty/regex
                - value?: any

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict          # 同“单请求字段”（不含 extract/asserts）
                - item.extract?: dict[str,str]
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 支持对 events / status / url / elapsed_ms 做字段提取与断言校验
          - 结果写入 data.extract / data.asserts / data.assert_summary
          - 若 max_events 达到即提前返回
        """

        args = {"payload": payload, "concurrency": concurrency}

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
          payload: dict  # WebSocket 入口（支持单请求或 items 并发）
            - env?: dict
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}

            - 单请求（直接顶层字段）:
              - url: str                  # ws:// 或 wss://
              - headers?: dict[str,str]   # 叠加 env.headers
              - sends?: list[str]
              - timeout?: float
              - max_messages?: int=10
              - extract?: dict[str,str]   # 例如 {"first":"messages.0"}
              - asserts?: list[dict]
                - path: str
                - op: str                 # eq/ne/gt/ge/lt/le/contains/in/exists/empty/not_empty/regex
                - value?: any

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict      # 同“单请求字段”（不含 extract/asserts）
                - item.extract?: dict[str,str]
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 支持对 messages / url / elapsed_ms / error 做字段提取与断言校验
          - 结果写入 data.extract / data.asserts / data.assert_summary
          - 连接正常关闭会提前停止收消息
        """

        args = {"payload": payload, "concurrency": concurrency}

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
          payload: dict  # GraphQL 入口（支持单请求或 items 并发）
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]
            - options?: {fail_fast?: bool=True}

            - 单请求（直接顶层字段）:
              - url: str
              - base_url?: str
              - headers?: dict[str,str]
              - params?: dict[str,any]
              - query: str
              - variables?: dict[str,any]
              - operation_name?: str
              - operationName?: str         # alias
              - timeout?: float
              - retries?: int
              - follow_redirects?: bool=True
              - extract?: dict[str,str]     # 例如 {"uid":"response.body_json.data.user.id"}
              - asserts?: list[dict]
                - path: str
                - op: str                   # eq/ne/gt/ge/lt/le/contains/in/exists/empty/not_empty/regex
                - value?: any

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict        # 同“单请求字段”（不含 extract/asserts）
                - item.extract?: dict[str,str]
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 底层复用 HTTP POST JSON 请求
          - 若响应 body_json.errors 非空，则判定 ok=False
          - 支持对 response / graphql 做字段提取与断言校验
          - 结果写入 data.extract / data.asserts / data.assert_summary
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
