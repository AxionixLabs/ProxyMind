# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import sys

from .app import TuiApp
from .runtime import LiveStreamProvider, open_runtime, runtime_labels


async def run() -> int:
    """运行独立终端界面并返回退出码。"""
    async with open_runtime() as runtime:
        pref_config = await runtime.fresh_pref_config(ttl_sec=0.0)
        model_label, workspace_label = runtime_labels(runtime, pref_config)
        await TuiApp(
            stream_provider=LiveStreamProvider(runtime),
            model_label=model_label,
            workspace_label=workspace_label
        ).run()
    return 0


def main() -> int:
    """执行终端界面模块入口。"""
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
