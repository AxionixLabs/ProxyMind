# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import asyncio
from mind_core.design import Design
from engine.tinker import MindError
from mind_app.mind_entry import main as _main


async def main() -> int:
    """兼容入口：转交到 `mind_app` 的应用入口。"""
    return await _main(entry_file=__file__)


if __name__ == "__main__":
    try:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
        exit_code = main_loop.run_until_complete(main())
    except MindError as _error:
        Design.Doc.err(_error)
        Design.show_outro()
        sys.exit(1)
    except KeyboardInterrupt:
        Design.show_outro()
        sys.exit(130)
    except asyncio.CancelledError:
        Design.show_outro()
        sys.exit(130)
    else:
        Design.show_outro()
        sys.exit(int(exit_code))
