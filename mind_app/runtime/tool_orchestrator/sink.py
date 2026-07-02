# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova import request
from .models import (
    ToolCall, ToolOutput
)


class ToolResultSink:
    """工具结果回填出口。"""

    @staticmethod
    async def post(call: ToolCall, output: ToolOutput) -> None:
        """按原始 call_id/name/execution 回填工具结果。"""
        await request.post_tool_result(
            call.cid,
            call.sid,
            call.call_id,
            call.name,
            output.ok,
            output.fields,
            execution=call.execution
        )


if __name__ == '__main__':
    pass
