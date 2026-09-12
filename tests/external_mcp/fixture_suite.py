"""启动 H/S 远端 fixture 并生成专用 MCP 服务配置，Ctrl+C 回收本进程创建的服务。"""

import argparse
import asyncio
import contextlib
from pathlib import Path

from tests.external_mcp.fixtures import (
    FixtureSpec,
    client_arguments,
    close_fixture_process,
    remote_fixture,
    write_config,
)


async def serve(directory: Path, *, repository: Path, launch_client: bool = False) -> None:
    """在新验收目录中持有真实远端服务，stdio 服务由被验收客户端启动。"""
    if not (repository / "mind.py").is_file():
        raise ValueError("fixture repository must contain the mind.py entrypoint")
    if directory.exists():
        raise FileExistsError("choose a new fixture directory to preserve previous evidence")
    directory.mkdir(parents=True)
    async with contextlib.AsyncExitStack() as stack:
        http = await stack.enter_async_context(remote_fixture(FixtureSpec(directory, "H", "streamable_http", repository=repository)))
        sse = await stack.enter_async_context(remote_fixture(FixtureSpec(directory, "S", "sse", repository=repository)))
        path = write_config(directory, (http, sse), repository=repository)
        print(f"Fixture config: {path}", flush=True)
        if launch_client:
            process = await asyncio.create_subprocess_exec(*client_arguments(path, repository=repository), cwd=directory)
            try:
                await process.wait()
            finally:
                await close_fixture_process(process)
            return
        print("Ctrl+C closes the fixture HTTP/SSE servers.", flush=True)
        await asyncio.Event().wait()


def main() -> None:
    """读取独立产物目录，不向用户配置写入任何内容。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd(), help="Repository root; defaults to the current directory.")
    parser.add_argument("--client", action="store_true", help="Run the real client with only the fixture MCP services.")
    values = parser.parse_args()
    try:
        asyncio.run(serve(values.directory.resolve(), repository=values.repository.resolve(), launch_client=values.client))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
