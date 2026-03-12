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
            - env?: dict                        # 默认请求环境；支持模板展开
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]              # 模板上下文；env/request/extract/asserts 均可用 {{expr}}
            - options?: dict                    # 支持模板展开
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则；支持模板展开
              - alias: path
            - asserts?: list[dict]              # 顶层单请求断言规则；支持模板展开
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:          # 支持模板展开
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
              - save_response?: bool=False      # 若响应体为 image/* 或 video/*，是否保存到本地
              - save_dir?: str                  # 媒体保存根目录

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”；支持模板展开
                - item.extract?: dict[str,str]  # 支持模板展开
                  - alias: path
                - item.asserts?: list[dict]     # 支持模板展开
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1                    # items 并发度（Semaphore）
        R: CTR
        N:
          - 单请求：payload 顶层请求字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.base_url / env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板语法：支持 {{expr}}；可引用 vars 中的上下文变量
          - 模板边界：仅支持受限表达式；不支持函数调用、导入、推导式等危险语法
          - 响应证据统一写入 data.request / data.response
          - 若响应 Content-Type 为 image/* 或 video/*，会识别为媒体响应并写入 response.media[]
          - save_response=true 时，媒体会落盘，并写入 response.media[].path / filename / mime_type / size
          - 可通过 response.media[].kind / path / filename / mime_type / size 做提取与断言
          - 仅负责请求、提取与断言证据采集；是否通过可继续交给 suffix / 大模型总结
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
          payload: dict  # SSE 拉流入口（支持单请求或 items 并发）
            - env?: dict                        # 默认请求环境；支持模板展开
              - base_url?: str
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]              # 模板上下文；env/request/extract/asserts 均可用 {{expr}}
            - options?: dict                    # 支持模板展开
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则；支持模板展开
              - alias: path
            - asserts?: list[dict]              # 顶层单请求断言规则；支持模板展开
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:          # 支持模板展开
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
              - max_events?: int=None           # 达到该数量后提前返回
              - media_index?: int               # 从 events 中选择第几个事件做媒体抽取；默认首个
              - media_path?: str                # 在目标事件内按路径提取媒体字段
              - save_response?: bool=False      # 抽到媒体后是否保存到本地
              - save_dir?: str                  # 媒体保存根目录

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”；支持模板展开
                - item.extract?: dict[str,str]  # 支持模板展开
                  - alias: path
                - item.asserts?: list[dict]     # 支持模板展开
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层请求字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.base_url / env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板语法：支持 {{expr}}；可引用 vars 中的上下文变量
          - 模板边界：仅支持受限表达式；不支持函数调用、导入、推导式等危险语法
          - SSE 事件按 response.events[] 返回；每项结构为 {event,id,data}
          - 达到 max_events 后会提前返回；否则在流结束后返回已采集 events
          - 响应证据统一写入 data.request / data.response
          - 支持通过 media_index / media_path 从 SSE events 中提取媒体引用，并写入 response.media[]
          - save_response=true 时，提取到的媒体会落盘，并写入 response.media[].path / filename / mime_type / size
          - 可通过 response.media[].kind / path / filename / mime_type / size 做提取与断言
          - 仅负责 SSE 请求、事件采集、提取与断言证据整理
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
          payload: dict  # WebSocket 入口（支持单请求或 items 并发）
            - env?: dict                        # 默认请求环境；支持模板展开
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]              # 模板上下文；env/request/extract/asserts 均可用 {{expr}}
            - options?: dict                    # 支持模板展开
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则；支持模板展开
              - alias: path
            - asserts?: list[dict]              # 顶层单请求断言规则；支持模板展开
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:          # 支持模板展开
              - url: str                        # ws:// 或 wss://
              - headers?: dict[str,str]         # 叠加 env.headers
              - sends?: list[str]
              - timeout?: float                 # 覆盖 env.timeout
              - max_messages?: int=10
              - media_index?: int               # 从 messages 中选择第几个消息做媒体抽取；默认首个
              - media_path?: str                # 在目标消息内按路径提取媒体字段
              - save_response?: bool=False      # 抽到媒体后是否保存到本地
              - save_dir?: str                  # 媒体保存根目录

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”；支持模板展开
                - item.extract?: dict[str,str]  # 支持模板展开
                  - alias: path
                - item.asserts?: list[dict]     # 支持模板展开
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层请求字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板语法：支持 {{expr}}；可引用 vars 中的上下文变量
          - 模板边界：仅支持受限表达式；不支持函数调用、导入、推导式等危险语法
          - 收到 ConnectionClosedOK 时会提前停止收消息
          - 最多采集 max_messages 条消息；原始消息写入 response.messages[]
          - 内部会尝试对每条消息做 JSON 解析，仅用于媒体路径提取；原始返回仍以 messages[] 为准
          - 响应证据统一写入 data.request / data.response
          - 支持通过 media_index / media_path 从 WS 消息中提取媒体引用，并写入 response.media[]
          - save_response=true 时，提取到的媒体会落盘，并写入 response.media[].path / filename / mime_type / size
          - 可通过 response.media[].kind / path / filename / mime_type / size 做提取与断言
          - 仅负责 WS 消息采集、提取与断言证据整理
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
        A: nexus_ws
        P:
          payload: dict  # WebSocket 入口（支持单请求或 items 并发）
            - env?: dict                        # 默认请求环境；支持模板展开
              - headers?: dict[str,str]
              - timeout?: float

            - vars?: dict[str,any]              # 模板上下文；env/request/extract/asserts 均可用 {{expr}}
            - options?: dict                    # 支持模板展开
              - fail_fast?: bool=True

            - extract?: dict[str,str]           # 顶层单请求提取规则；支持模板展开
              - alias: path
            - asserts?: list[dict]              # 顶层单请求断言规则；支持模板展开
              - path: str
              - op: str
              - value?: any

            - 单请求（直接顶层字段）:          # 支持模板展开
              - url: str                        # ws:// 或 wss://
              - headers?: dict[str,str]         # 叠加 env.headers
              - sends?: list[str]
              - timeout?: float                 # 覆盖 env.timeout
              - max_messages?: int=10
              - media_index?: int               # 从 messages 中选择第几个消息做媒体抽取；默认首个
              - media_path?: str                # 在目标消息内按路径提取媒体字段
              - save_response?: bool=False      # 抽到媒体后是否保存到本地
              - save_dir?: str                  # 媒体保存根目录

            - 批请求（并发）:
              - items?: list[dict]
                - item.name?: str
                - item.request: dict            # 同“单请求字段”；支持模板展开
                - item.extract?: dict[str,str]  # 支持模板展开
                  - alias: path
                - item.asserts?: list[dict]     # 支持模板展开
                  - path: str
                  - op: str
                  - value?: any

          concurrency: int=1
        R: CTR
        N:
          - 单请求：payload 顶层请求字段 + 顶层 extract/asserts 生效
          - 批请求：payload.items 生效；每个 item 可独立定义 extract/asserts
          - env.headers / env.timeout 作为默认值，可被 request 同名字段覆盖
          - headers 为叠加：request.headers 覆盖 env.headers 同名键
          - 模板作用范围：env / options / request / extract / asserts
          - 模板语法：支持 {{expr}}；可引用 vars 中的上下文变量
          - 模板边界：仅支持受限表达式；不支持函数调用、导入、推导式等危险语法
          - 收到 ConnectionClosedOK 时会提前停止收消息
          - 最多采集 max_messages 条消息；原始消息写入 response.messages[]
          - 内部会尝试对每条消息做 JSON 解析，仅用于媒体路径提取；原始返回仍以 messages[] 为准
          - 响应证据统一写入 data.request / data.response
          - 支持通过 media_index / media_path 从 WS 消息中提取媒体引用，并写入 response.media[]
          - save_response=true 时，提取到的媒体会落盘，并写入 response.media[].path / filename / mime_type / size
          - 可通过 response.media[].kind / path / filename / mime_type / size 做提取与断言
          - 仅负责 WS 消息采集、提取与断言证据整理
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
