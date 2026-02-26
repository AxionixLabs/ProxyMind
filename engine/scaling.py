#  ____            _ _
# / ___|  ___ __ _| (_)_ __   __ _
# \___ \ / __/ _` | | | '_ \ / _` |
#  ___) | (_| (_| | | | | | | (_| |
# |____/ \___\__,_|_|_|_| |_|\__, |
#                            |___/
#

from dataclasses import dataclass


@dataclass
class PackItem:
    name: str
    message: str
    meta: dict[str, str]


class Pack(object):

    CFG_KEYS = (
        "loop_prefix", "loop_suffix",
        "round_prefix", "round_suffix",
        "global_prefix", "global_suffix",
    )

    @staticmethod
    def pack_parse(text: str) -> tuple[list[PackItem], dict[str, str]]:
        """
        返回: (items, cfg)

        cfg 里包含：
          loop_prefix/loop_suffix
          round_prefix/round_suffix
          global_prefix/global_suffix

        顶部全局配置区：第一个 '---' 之前的连续 '#...' 行
        用例区：从第一个 '---' 之后开始，每个块一个用例
        """

        def strip_hash(char: str) -> str:
            return char.lstrip()[1:].strip()

        def looks_like_key(raw: str) -> bool:
            if ":" not in raw:
                return False
            head = raw.split(":", 1)[0].strip()
            return bool(head) and all(ch.isalnum() or ch in "_-" for ch in head.lower())

        def parse_meta_lines(meta_chars: list[str]) -> dict[str, str]:
            """
            支持：
              # key: value
              # key:
              # line1
              # line2
            """
            meta_dict: dict[str, str] = {}
            index = 0
            while index < len(meta_chars):
                raw = strip_hash(meta_chars[index])
                index += 1
                if not raw or ":" not in raw:
                    continue

                k, v = raw.split(":", 1)
                key = k.strip().lower()
                val = v.strip()

                if val == "":
                    buf: list[str] = []
                    while index < len(meta_chars):
                        nxt = strip_hash(meta_chars[index])
                        if looks_like_key(nxt):
                            break
                        buf.append(nxt)
                        index += 1
                    meta_dict[key] = "\n".join(buf).strip()
                else:
                    meta_dict[key] = val
            return meta_dict

        lines = text.splitlines()

        # ---------- 1) 解析顶部全局配置区 ----------
        global_meta_lines: list[str] = []
        rest_from = 0

        for idx, line in enumerate(lines):
            if line.strip() == "---":
                rest_from = idx + 1
                break

            if line.lstrip().startswith("#"):
                global_meta_lines.append(line)
                continue

            if line.strip():
                # 顶部出现正文：认为没有全局配置区
                global_meta_lines = []
                rest_from = 0
                break

        g = parse_meta_lines(global_meta_lines) if global_meta_lines else {}
        cfg: dict[str, str] = {k: (g.get(k) or "").strip() for k in Pack.CFG_KEYS}

        # ---------- 2) 切分用例 blocks（从 rest_from 开始） ----------
        blocks: list[list[str]] = []
        cur: list[str] = []

        for line in lines[rest_from:]:
            if line.strip() == "---":
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            cur.append(line)
        if cur:
            blocks.append(cur)

        # ---------- 3) 逐块解析用例 ----------
        items: list[PackItem] = []
        auto_idx = 0

        for b in blocks:
            while b and not b[0].strip():
                b.pop(0)
            while b and not b[-1].strip():
                b.pop()
            if not b: continue

            # 块头连续 # 行
            meta_lines: list[str] = []
            i = 0
            while i < len(b) and b[i].lstrip().startswith("#"):
                meta_lines.append(b[i])
                i += 1

            meta = parse_meta_lines(meta_lines)

            msg = "\n".join(b[i:]).strip()
            if not msg: continue

            auto_idx += 1
            name = (meta.get("name") or f"item_{auto_idx:03d}").strip()

            # 注入全局 cfg 到每条 meta（方便执行层 fallback）
            meta.update(cfg)

            items.append(PackItem(name=name, message=msg, meta=meta))

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
