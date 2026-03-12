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
          payload: dict  # HTTP 入口（支持单请求或 items 并发）；支持模板 {{expr}}
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float
            - vars?: dict[str,any]                # 模板上下文
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]             # 顶层单请求提取规则；alias -> path
            - asserts?: list[dict]
              - path: str
              - op: str
              - value?: any

            - 单请求（直接放在 payload 顶层；支持模板展开）:
              - method?: str="GET"
              - url: str
              - base_url?: str
              - headers?: dict[str,str]
              - params?: dict[str,any]
              - json?: dict[str,any]
              - json_body?: dict[str,any]
              - body?: str
              - body_text?: str
              - form?: dict[str,any]
              - files?: list[dict]
                - field?: str="file"
                - path?: str
                - filename?: str
                - content_type?: str
                - text?: str
                - bytes?: bytes|str
              - timeout?: float
              - retries?: int=0
              - follow_redirects?: bool=True
              - save_response?: bool=False
              - save_dir?: str

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - payload.items 为空时按单请求执行；非空时按批请求执行
          - env.base_url / env.timeout 为默认值；可被请求同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板上下文仅来自 vars；各 item 独立渲染，不共享变量结果
          - 提取与断言基于返回 data 执行，常用路径起点为 request.* / response.*
          - 若响应体为 image/* 或 video/*，会写入 response.media[]
          - save_response=true 时，媒体可落盘，并补充 path / filename / mime_type / size
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
          payload: dict  # SSE 入口（支持单请求或 items 并发）；支持模板 {{expr}}
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float
            - vars?: dict[str,any]                # 模板上下文
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]
              - alias: path
            - asserts?: list[dict]
              - path: str
              - op: str
              - value?: any

            - 单请求（直接放在 payload 顶层；支持模板展开）:
              - method?: str="GET"
              - url: str
              - base_url?: str
              - headers?: dict[str,str]
              - params?: dict[str,any]
              - json?: dict[str,any]
              - json_body?: dict[str,any]
              - body?: str
              - body_text?: str
              - form?: dict[str,any]
              - files?: list[dict]
                - field?: str="file"
                - path?: str
                - filename?: str
                - content_type?: str
                - text?: str
                - bytes?: bytes|str
              - timeout?: float
              - retries?: int=0
              - follow_redirects?: bool=True
              - max_events?: int
              - media_index?: int
              - media_path?: str
              - save_response?: bool=False
              - save_dir?: str

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - payload.items 为空时按单请求执行；非空时按批请求执行
          - env.base_url / env.timeout 为默认值；可被请求同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板上下文仅来自 vars；各 item 独立渲染，不共享变量结果
          - response.events[] 结构固定为 {event, id, data}
          - max_events 达到上限会提前返回；否则在流结束后返回
          - media_index / media_path 基于 response.events 提取媒体，结果写入 response.media[]
          - save_response=true 时，媒体可落盘，并补充 path / filename / mime_type / size
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
          payload: dict  # WebSocket 入口（支持单请求或 items 并发）；支持模板 {{expr}}
            - env?: dict
              - headers?: dict[str,str]
              - timeout?: float
            - vars?: dict[str,any]                # 模板上下文
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]
              - alias: path
            - asserts?: list[dict]
              - path: str
              - op: str
              - value?: any

            - 单请求（直接放在 payload 顶层；支持模板展开）:
              - url: str
              - headers?: dict[str,str]
              - sends?: list[str]
              - timeout?: float
              - max_messages?: int=10
              - media_index?: int
              - media_path?: str
              - save_response?: bool=False
              - save_dir?: str

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - payload.items 为空时按单请求执行；非空时按批请求执行
          - env.timeout 为默认值；可被请求同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板上下文仅来自 vars；各 item 独立渲染，不共享变量结果
          - 建连后按 sends 顺序发送；最多采集 max_messages 条消息
          - 遇到 ConnectionClosedOK 会提前停止接收
          - response.messages[] 保存原始消息字符串；内部会尝试解析 JSON 以支持 media_path 提取
          - media_index / media_path 基于消息内容提取媒体，结果写入 response.media[]
          - save_response=true 时，媒体可落盘，并补充 path / filename / mime_type / size
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
          payload: dict  # GraphQL 入口（支持单请求或 items 并发）；支持模板 {{expr}}
            - env?: dict
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float
            - vars?: dict[str,any]                # 模板上下文
            - options?: dict
              - fail_fast?: bool=True

            - extract?: dict[str,str]
              - alias: path
            - asserts?: list[dict]
              - path: str
              - op: str
              - value?: any

            - 单请求（直接放在 payload 顶层；支持模板展开）:
              - url: str
              - query: str
              - variables?: dict[str,any]
              - operation_name?: str
              - operationName?: str
              - base_url?: str
              - headers?: dict[str,str]
              - params?: dict[str,any]
              - timeout?: float
              - retries?: int=0
              - follow_redirects?: bool=True
              - media_path?: str
              - save_response?: bool=False
              - save_dir?: str

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict
                - item.extract?: dict[str,str]
                  - alias: path
                - item.asserts?: list[dict]
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - payload.items 为空时按单请求执行；非空时按批请求执行
          - env.base_url / env.timeout 为默认值；可被请求同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板上下文仅来自 vars；各 item 独立渲染，不共享变量结果
          - 实际请求固定为 HTTP POST，请求体为 {query, variables, operationName}
          - operation_name 与 operationName 二选一传入，内部统一映射为 operationName
          - 若 response.body_json.errors 非空，则整体 ok=False
          - GraphQL 额外证据写入 data.graphql={query, variables, operation_name, errors}
          - media_path 基于 response.body_json 提取媒体，结果写入 response.media[]
          - save_response=true 时，媒体可落盘，并补充 path / filename / mime_type / size
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
