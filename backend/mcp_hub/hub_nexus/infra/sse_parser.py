# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.models.model_nexus import SseEvent


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
