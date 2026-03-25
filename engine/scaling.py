# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
from dataclasses import dataclass


@dataclass
class PackItem:
    name: str
    message: str
    loop: int
    meta: dict[str, str]


class Pack(object):
    """Pack class."""

    CFG_KEYS = (
        "repeat",
        "pattern",
        "attempts",
        "stop_on_fail",
        "loop_prefix",
        "loop_suffix",
        "round_prefix",
        "round_suffix",
        "global_prefix",
        "global_suffix",
        "global_rule",
    )
    KEY_RE = re.compile(r"^[A-Za-z0-9_-]+\s*:\s*")

    @staticmethod
    def pack_parse(text: str) -> tuple[list[PackItem], dict[str, str]]:
        """
        返回: (items, cfg)

        顶部全局配置区：一个 ```cfg ... ``` 代码块（出现一次即可；建议放文件最前）
          - 支持 key: value
          - 支持 key: |  多行（后续缩进块）
          - 支持 key: <<<  多行（以 >>> 结束）
        用例区：cfg 块之外的内容按 `---` 分隔；每块可有 # key: value 的 meta
        """

        def strip_hash(line: str) -> str:
            """
            把 '# ' 去掉，但保留左侧缩进（用于 dedent_block 正确工作）
            """
            s = line.lstrip()
            if not s.startswith("#"):
                return s.rstrip("\n")
            # 去掉第一个 '#'
            s = s[1:]
            # 去掉紧随其后的一个空格（可选）
            if s.startswith(" "):
                s = s[1:]
            return s.rstrip("\n")

        def looks_like_key(raw: str) -> bool:
            """
            只把“行首就是 key:”当成 key，避免正文里出现 ':' 被误判
            """
            return bool(Pack.KEY_RE.match(raw.strip()))

        def parse_meta_lines(meta_lines: list[str]) -> dict[str, str]:
            """
            支持：
              # key: value
              # key:
              #   line1
              #   line2

            并新增支持（与 cfg 一致）：
              # key: |
              #   line1
              #   line2

              # key: <<<
              # line1
              # line2
              # >>>
            """
            meta: dict[str, str] = {}
            i = 0

            while i < len(meta_lines):
                raw0 = strip_hash(meta_lines[i]).rstrip()
                i += 1

                if not raw0 or ":" not in raw0:
                    continue

                k, v = raw0.split(":", 1)
                key = k.strip().lower()
                val = v.strip()

                # --- key: |  缩进块（直到遇到下一条 key: 或 meta 结束）
                if val == "|":
                    buf: list[str] = []
                    while i < len(meta_lines):
                        nxt = strip_hash(meta_lines[i])
                        if looks_like_key(nxt):
                            break
                        buf.append(nxt.rstrip("\n"))
                        i += 1
                    meta[key] = dedent_block(buf)  # 复用你已有的 dedent_block
                    continue

                # --- key: <<< ... >>>  终止块
                if val == "<<<":
                    buf: list[str] = []
                    while i < len(meta_lines):
                        nxt = strip_hash(meta_lines[i])
                        i += 1
                        if nxt.strip() == ">>>":
                            break
                        buf.append(nxt.rstrip("\n"))
                    meta[key] = "\n".join(buf).rstrip()
                    continue

                # --- key:  后续多行（直到下一条 key: 或 meta 结束）
                if val == "":
                    buf: list[str] = []
                    while i < len(meta_lines):
                        nxt = strip_hash(meta_lines[i])
                        if looks_like_key(nxt):
                            break
                        buf.append(nxt.rstrip("\n"))
                        i += 1
                    # 这里你也可以选择 dedent（更一致），我建议 dedent
                    meta[key] = dedent_block(buf)
                    continue

                # --- key: value
                meta[key] = val

            return meta

        def dedent_block(block: list[str]) -> str:
            """
            删除多行块的公共缩进（类似 YAML 的 |）
            """
            non_blank = [ln for ln in block if ln.strip()]
            if not non_blank:
                return "\n".join(block).rstrip()

            def indent_len(s: str) -> int:
                n = 0
                for ch in s:
                    if ch == " ":
                        n += 1
                    elif ch == "\t":
                        n += 4
                    else:
                        break
                return n

            min_indent = min(indent_len(ln) for ln in non_blank)
            out: list[str] = []
            for ln in block:
                if not ln.strip():
                    out.append("")
                    continue
                # 去掉 min_indent（空格/Tab 混排简单处理：按空格优先）
                cut = min_indent
                j = 0
                while cut > 0 and j < len(ln):
                    if ln[j] == " ":
                        cut -= 1
                        j += 1
                    elif ln[j] == "\t":
                        cut -= 4
                        j += 1
                    else:
                        break
                out.append(ln[j:])
            return "\n".join(out).rstrip()

        def parse_cfg_block(cfg_lines: list[str]) -> dict[str, str]:
            """
            解析 ```cfg 内部：
              key: value
              key: | + 缩进块
              key: <<< ... >>>
            """
            config: dict[str, str] = {}
            i = 0
            while i < len(cfg_lines):
                line = cfg_lines[i]
                i += 1

                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if ":" not in line:
                    continue

                k, v = line.split(":", 1)
                key = k.strip().lower()
                val = v.strip()

                # key: |  多行缩进块
                if val == "|":
                    buf: list[str] = []
                    while i < len(cfg_lines):
                        ln = cfg_lines[i]
                        # 允许空行继续
                        if ln.strip() == "":
                            buf.append("")
                            i += 1
                            continue
                        # 必须缩进（至少 1 个空格/Tab）
                        if ln.startswith((" ", "\t")):
                            buf.append(ln.rstrip("\n"))
                            i += 1
                            continue
                        break
                    config[key] = dedent_block(buf)
                    continue

                # key: <<< ... >>>
                if val == "<<<":
                    buf: list[str] = []
                    while i < len(cfg_lines):
                        ln = cfg_lines[i]
                        i += 1
                        if ln.strip() == ">>>":
                            break
                        buf.append(ln.rstrip("\n"))
                    config[key] = "\n".join(buf).rstrip()
                    continue

                # key: value
                config[key] = val.strip()

            return config

        def extract_cfg(lines: list[str]) -> tuple[dict[str, str], list[str]]:
            """
            抽取第一个 ```cfg ... ``` 块，并从正文中移除。
            """
            start = None
            for idx, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith("```") and s[3:].strip().lower() == "cfg":
                    start = idx
                    break

            if start is None:
                return {}, lines

            end = None
            for j in range(start + 1, len(lines)):
                if lines[j].strip() == "```":
                    end = j
                    break

            if end is None:
                # cfg 块没闭合：当作没有 cfg（也可以改成 raise）
                return {}, lines

            cfg_lines = lines[start + 1 : end]
            rest = lines[:start] + lines[end + 1 :]

            config = parse_cfg_block(cfg_lines)
            return config, rest

        # 1) 拆行 + 抽 cfg
        src_lines = text.splitlines()
        cfg_raw, body_lines = extract_cfg(src_lines)

        # cfg 里至少保证 CFG_KEYS 都有（没有则空串）
        cfg: dict[str, str] = {
            key: (cfg_raw.get(key) or "").strip() for key in Pack.CFG_KEYS
        }
        # 允许额外字段（增强字段）也保留在 cfg 里
        for key, value in cfg_raw.items():
            if key not in cfg:
                cfg[key] = (value or "").strip() if isinstance(value, str) else str(value)

        # 2) 切分用例 blocks（用 ---）
        blocks: list[list[str]] = []
        cur: list[str] = []
        for body_line in body_lines:
            if body_line.strip() == "---":
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            cur.append(body_line)
        if cur:
            blocks.append(cur)

        # 3) 逐块解析用例
        items: list[PackItem] = []
        auto_idx = 0

        for b in blocks:
            # 去掉块首尾空行
            while b and not b[0].strip():
                b.pop(0)
            while b and not b[-1].strip():
                b.pop()
            if not b: continue

            # 块头连续 # 行
            src_meta_lines: list[str] = []
            index = 0
            while index < len(b) and b[index].lstrip().startswith("#"):
                src_meta_lines.append(b[index])
                index += 1

            src_meta = parse_meta_lines(src_meta_lines)

            message = "\n".join(b[index:]).strip()
            if not message: continue

            auto_idx += 1
            name = (src_meta.get("name") or f"item_{auto_idx:03d}").strip()

            try:
                item_loop = int(src_meta.get("loop") or 1)
            except (TypeError, ValueError):
                item_loop = 1

            if item_loop < 1:
                item_loop = 1

            merged = dict(cfg)
            
            # 覆盖 cfg
            merged.update(src_meta)  
            src_meta = merged

            items.append(
                PackItem(
                    name=name,
                    message=message,
                    loop=item_loop,
                    meta=src_meta
                )
            )

        return items, cfg

    @staticmethod
    def brief_err(exc: BaseException) -> str:
        parts = [f"{type(x).__name__}: {x}" for x in Pack.flatten(exc)]
        return " | ".join(parts[:3])

    @staticmethod
    def flatten(exc: BaseException) -> list[BaseException]:
        if isinstance(exc, BaseExceptionGroup):
            out: list[BaseException] = []
            for sub in exc.exceptions:
                out.extend(Pack.flatten(sub))
            return out
        return [exc]


if __name__ == '__main__':
    pass
