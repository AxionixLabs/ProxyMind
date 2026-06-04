# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import uuid
from pathlib import Path
from backend.utilities.storage.roots import output_base_dir


def mk_out_dir(output_dir: str, engine: str, tool: str) -> Path:
    """按引擎名、工具名和时间戳创建隔离输出目录。"""
    base_dir = output_base_dir(output_dir)
    tag      = f"{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    out_dir = base_dir / engine / tool / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    return out_dir


if __name__ == '__main__':
    pass
