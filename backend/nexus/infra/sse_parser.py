#  ____ ____  _____   ____
# / ___/ ___|| ____| |  _ \ __ _ _ __ ___  ___ _ __
# \___ \___ \|  _|   | |_) / _` | '__/ __|/ _ \ '__|
#  ___) |__) | |___  |  __/ (_| | |  \__ \  __/ |
# |____/____/|_____| |_|   \__,_|_|  |___/\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.nexus.domain.model import SseEvent


class SseParser(object):

    @staticmethod
    def parse_block(block: str) -> typing.Optional[SseEvent]:
        """把单个 SSE 文本块解析成结构化事件。"""
        if not (raw := block.strip("\r\n")).strip():
            return None

        event = SseEvent(event=None, data="", id=None)
        data_lines: list[str] = []

        for line in raw.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            key, value = line.split(":", 1)

            key   = key.strip()
            value = value.lstrip()

            if key == "event":
                event.event = value
            elif key == "data":
                data_lines.append(value)
            elif key == "id":
                event.id = value

        event.data = "\n".join(data_lines)
        return event


if __name__ == '__main__':
    pass
