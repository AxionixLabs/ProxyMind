# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.schemas.schema_k6 import (
    ScriptTextArg,
    ScriptFileArg,
    ScriptNameArg,
    WorkDirArg,
    VusArg,
    DurationArg,
    IterationsArg,
    EnvArg,
    TagsArg,
    SummaryExportArg,
    ExtraArgsArg,
    ExecutionModeArg,
    ResponseCaptureArg,
    ResponseExportArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:
    """注册通用接口执行工具。"""

    @mcp.tool(
        description=(
            "执行一次压力测试。默认按压测工具理解。"
            " 只有当你明确要把它用于接口测试时，才按接口测试语义使用。"
            " `env` 只用于运行时模板变量，不承载核心执行配置。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "k6"}
    )
    @task_middleware("perf_run")
    async def perf_run(
        script_text: ScriptTextArg,
        script_name: ScriptNameArg = None,
        env: EnvArg = None,
        summary_export: SummaryExportArg = None,
        extra_args: ExtraArgsArg = None,
        execution_mode: ExecutionModeArg = "auto",
        response_capture: ResponseCaptureArg = "auto",
        response_export: ResponseExportArg = None
    ) -> CallToolResult:
        """执行一次压力测试。默认按压测语义使用。"""
        await Requires.connect_k6()

        args = {
            "script_text"      : script_text,
            "script_name"      : script_name,
            "env"              : env,
            "summary_export"   : summary_export,
            "extra_args"       : extra_args,
            "execution_mode"   : execution_mode,
            "response_capture" : response_capture,
            "response_export"  : response_export
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.k6.agent_id}.perf_run", args=args)
            try:
                return await ctx.k6.run_inline(
                    script_text=typing.cast(str, script_text),
                    script_name=script_name,
                    env=env,
                    summary_export=summary_export,
                    extra_args=extra_args,
                    execution_mode=execution_mode,
                    response_capture=response_capture,
                    response_export=response_export
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="perf_run",
            args=args,
            target_list=[ctx.k6],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一个本地已有的 JS/k6 脚本文件。"
            " 仅用于现成脚本文件；如果你拿到的是脚本文本，请使用 `perf_run`。"
            " `env` 只用于运行时模板变量，不承载核心执行配置。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "k6"}
    )
    @task_middleware("perf_run_file")
    async def perf_run_file(
        script_file: ScriptFileArg,
        workdir: WorkDirArg = None,
        vus: VusArg = None,
        duration: DurationArg = None,
        iterations: IterationsArg = None,
        env: EnvArg = None,
        tags: TagsArg = None,
        summary_export: SummaryExportArg = None,
        extra_args: ExtraArgsArg = None
    ) -> CallToolResult:
        """执行本地脚本文件。"""
        await Requires.connect_k6()

        args = {
            "script_file"    : script_file,
            "workdir"        : workdir,
            "vus"            : vus,
            "duration"       : duration,
            "iterations"     : iterations,
            "env"            : env,
            "tags"           : tags,
            "summary_export" : summary_export,
            "extra_args"     : extra_args
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.k6.agent_id}.perf_run_file", args=args)
            try:
                return await ctx.k6.run_file(
                    script_file=typing.cast(str, script_file),
                    workdir=workdir,
                    vus=vus,
                    duration=duration,
                    iterations=iterations,
                    env=env,
                    tags=tags,
                    summary_export=summary_export,
                    extra_args=extra_args,
                    tool="perf_run_file"
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="perf_run_file",
            args=args,
            target_list=[ctx.k6],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
