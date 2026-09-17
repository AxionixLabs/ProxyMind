# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.mcp_elicitation import (
    ElicitationRequest,
    ElicitationResponse,
)


class McpElicitationHandler(typing.Protocol):
    """把连接内的已校验请求交给现有交互队列；取消必须撤下对应表面，不创建授权事实。"""

    async def request_elicitation(self, request: ElicitationRequest) -> ElicitationResponse:
        """等待当前用户输入，调用结束、连接关闭或超时后不得交付迟到答案。"""
        ...


@typing.runtime_checkable
class McpElicitationPresenter(typing.Protocol):
    """标记实际支持表单和 URL 的交互前端；只展示队列当前项，不持有排队或连接状态。"""

    async def present_elicitation(self, request: ElicitationRequest) -> ElicitationResponse:
        """读取当前交互，在任意取消路径清理表面和输入正文。"""
        ...


if __name__ == '__main__':
    pass
