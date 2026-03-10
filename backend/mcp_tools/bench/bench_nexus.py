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

            - vars?: dict[str,any]              # {{k}} 模板变量
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则
              - alias: path
            - asserts?: list[dict]             # 顶层单请求断言规则
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:
              - method?: str="GET"
              - url: str
              - base_url?: str                  # 覆盖 env.base_url
              - headers?: dict[str,str]         # 叠加 env.headers
              - params?: dict[str,any]
              - json?: dict[str,any]
              - json_body?: dict[str,any]       # alias
              - body?: str
              - body_text?: str                 # alias
              - form?: dict[str,any]
              - files?: list[dict]
                - field?: str="file"
                - path?: str
                - filename?: str
                - content_type?: str
                - text?: str
                - bytes?: bytes|str
              - timeout?: float                 # 覆盖 env.timeout
              - retries?: int=0
              - follow_redirects?: bool=True

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1                    # items 并发度（Semaphore）
        R: CTR
        N:
          - 单请求：payload 顶层 request 字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.base_url / env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 仅负责请求、提取与断言证据采集；是否通过可继续交给 suffix / 大模型总结
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
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则
              - alias: path
            - asserts?: list[dict]             # 顶层单请求断言规则
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:
              - method?: str="GET"
              - url: str
              - base_url?: str                  # 覆盖 env.base_url
              - headers?: dict[str,str]         # 叠加 env.headers
              - params?: dict[str,any]
              - json?: dict[str,any]
              - json_body?: dict[str,any]       # alias
              - body?: str
              - body_text?: str                 # alias
              - form?: dict[str,any]
              - files?: list[dict]
                - field?: str="file"
                - path?: str
                - filename?: str
                - content_type?: str
                - text?: str
                - bytes?: bytes|str
              - timeout?: float                 # 覆盖 env.timeout
              - retries?: int=0
              - follow_redirects?: bool=True
              - max_events: int?=None

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层 request 字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.base_url / env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 达到 max_events 后会提前返回；否则在流结束后返回已采集 events
          - 仅负责 SSE 请求、事件采集、提取与断言证据整理
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
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则
              - alias: path
            - asserts?: list[dict]             # 顶层单请求断言规则
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:
              - url: str                        # ws:// 或 wss://
              - headers?: dict[str,str]         # 叠加 env.headers
              - sends?: list[str]
              - timeout?: float                 # 覆盖 env.timeout
              - max_messages?: int=10

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层 request 字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 收到 ConnectionClosedOK 时会提前停止收消息
          - 最多采集 max_messages 条消息
          - 仅负责 WS 消息采集、提取与断言证据整理
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
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则
              - alias: path
            - asserts?: list[dict]             # 顶层单请求断言规则
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:
              - url: str
              - query: str
              - variables?: dict[str,any]
              - operation_name?: str
              - operationName?: str             # alias
              - base_url?: str                  # 覆盖 env.base_url
              - headers?: dict[str,str]         # 叠加 env.headers
              - params?: dict[str,any]
              - timeout?: float                 # 覆盖 env.timeout
              - retries?: int=0
              - follow_redirects?: bool=True

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层 request 字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.base_url / env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 底层复用 HTTP POST JSON 请求
          - 若 response.body_json.errors 非空，则额外判定 ok=False
          - 仅负责请求、提取与断言证据采集
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
