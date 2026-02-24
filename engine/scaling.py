#  ____            _ _
# / ___|  ___ __ _| (_)_ __   __ _
# \___ \ / __/ _` | | | '_ \ / _` |
#  ___) | (_| (_| | | | | | | (_| |
# |____/ \___\__,_|_|_|_| |_|\__, |
#                            |___/
#

import typing
from dataclasses import dataclass


@dataclass
class PackItem:
    name: str
    message: str
    meta: dict[str, str]


@dataclass
class PackItemResult:
    name: str
    ok: bool
    cost_s: float
    error: typing.Optional[str] = None


@dataclass
class PackReport:
    total: int
    executed: int
    ok: int
    failed: int
    skipped: int
    results: list[PackItemResult]


def pack_parse(text: str) -> list[PackItem]:
    """
    将 .md/.txt 文本解析为 PackItem 列表（自然语言用例序列）。

    约定格式：
    - 用例分隔符：某一行 strip() 后等于 '---'，表示一个用例块结束
    - 每个用例块允许在开头写若干行元信息（只解析连续的开头 # 行）：
        # key: value
      目前约定支持：name（可选）
    - 元信息结束后，剩余内容原样拼接为自然语言 message（交给 stream_plan 生成步骤序列）

    返回：
      list[PackItem(name, message, meta)]
    """

    # 1) 按分隔符切分为多个块（每个块对应一个用例）
    blocks: list[list[str]] = []
    cur: list[str] = []

    for line in text.splitlines():
        if line.strip() == "---":
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)

    if cur:
        blocks.append(cur)

    items: list[PackItem] = []
    auto_idx = 0

    # 2) 逐块解析：块头元信息 + 自然语言正文
    for b in blocks:
        # 去掉块前后的空行，避免产生空用例
        while b and not b[0].strip():
            b.pop(0)
        while b and not b[-1].strip():
            b.pop()
        if not b: continue

        meta: dict[str, str] = {}
        i = 0

        # 只解析块开头连续的 # 行作为元信息；一旦遇到非 # 行即停止解析
        while i < len(b) and b[i].lstrip().startswith("#"):
            raw = b[i].lstrip()[1:].strip()  # 去掉开头 '#'
            if ":" in raw:
                k, v = raw.split(":", 1)
                meta[k.strip().lower()] = v.strip()
            i += 1

        # 将剩余内容作为自然语言 message（会交给 plan 模式/模型编译为 steps）
        msg = "\n".join(b[i:]).strip()
        if not msg: continue

        # name 可选，不写则生成 item_001 / item_002 ...
        auto_idx += 1
        name = meta.get("name") or f"item_{auto_idx:03d}"

        items.append(PackItem(name=name, message=msg, meta=meta))

    return items


if __name__ == '__main__':
    pass
