import re
import sys
import signal
import typing
import asyncio
from pathlib import Path
from loguru import logger
from rich.prompt import Prompt
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client
from engine.design import Design
from engine.manage import ServerManage
from engine.parser import Parser
from engine.terminal import Terminal
from engine.tinker import Active
from utils import request


def signal_processor(*_, **__) -> None:
    Design.console.print()
    logger.info(f"📞 Received signal {signal.SIGINT} ...")
    sys.exit(0)


async def mind_trip(
    message: str,
    model: typing.Union[
        str,
        typing.Literal[
            "compound-beta",
            "compound-beta-mini",
            "gemma2-9b-it",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-guard-4-12b",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3-32b",
        ],
    ] = "llama-3.1-8b-instant"
) -> None:

    async def exec_looper() -> None:
        for i in range(plan.get("loop_count", 1)):
            for step in steps:
                action = step["action"]
                result = await session.call_tool(action["action"], action["args"])

                if result.isError: return logger.error(result.content[0].text)
                else: logger.info(f"{result.content[0].text}")

    async with streamable_http_client("http://127.0.0.1:3333/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            list_tools: ListToolsResult = await session.list_tools()

            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description" : tool.description,
                        "parameters"  : tool.inputSchema
                    }
                }
                for tool in list_tools.tools
            ]

            for tool in openai_tools: logger.debug(f"⚙️ Tool {tool['function']['name']}")

            payload = {"model": model, "message": message, "tools": openai_tools}

            async for plan in request.stream_planner(payload):
                if not (steps := plan.get("steps")):
                    continue
                await exec_looper()


async def mind_loop() -> None:
    doc = """\

    /help              显示帮助
    /quit              退出
    /repeat N <goal>   将目标重复执行 N 次
    /tools             查看可用工具（如果你有输出方法）
    """

    ask = "[bold #AFD7FF]\n🤔 Mind 输入目标或 /help[/]"

    while (raw := Prompt.ask(ask)) not in {"/quit", "quit", "exit"}:
        if raw.strip() in {"/help", "help"}:
            Design.console.print(doc); continue

        if m := re.match(r"^/repeat\s+(\d+)\s+(.+)$", raw.strip()):
            await mind_trip(f"{m.group(2)}，循环 {int(m.group(1))} 次"); continue

        await mind_trip(raw)


async def mind_boot() -> ServerManage:
    pass


async def main() -> None:
    parser = Parser()

    Active.active("DEBUG")

    root = Path(__file__).parent

    # program = Path(root, "mcp_app", "mcp_server.py")
    program = Path(root, "applications", "mcp_server.app", "Contents", "MacOS", "mcp_server")
    # program = Path(root, "applications", "mcp_server.dist", "mcp_server.exe")

    if sys.platform == "darwin":
        await Terminal.cmd_line(["chmod", "+x", program])

    server = ServerManage()

    if program.name.endswith("py"):
        await server.mcp_begin([sys.executable, str(program)])
    else:
        await server.mcp_begin([str(program)])

    signal.signal(signal.SIGINT, signal_processor)

    try:
        cmd_lines = parser.parse_cmd

        if focus := cmd_lines.focus: await mind_trip(focus)
        else: await mind_loop()

    except Exception as e: logger.error(e); raise e

    finally: await server.mcp_final()


if __name__ == '__main__':
    asyncio.run(main())
