# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import ModelCapabilityError
from agent.protocol import ReviewStreamRequest
from protocol.schema.review import (
    MindReviewRequest,
    parse_mind_review_request,
)

__all__ = (
    "require_review_tools",
    "wire_review_request",
)


def wire_review_request(request: ReviewStreamRequest) -> MindReviewRequest:
    """在 HTTP 边界把本地冻结请求重新校验为正式 wire 请求。"""
    if not isinstance(request, ReviewStreamRequest):
        raise TypeError("review stream request is required")
    return parse_mind_review_request(request.to_dict())


def require_review_tools(request: ReviewStreamRequest) -> None:
    """拒绝没有冻结只读仓库能力的 Review 请求。"""
    if request.has_read_only_tools:
        return
    raise ModelCapabilityError(
        "review_tools_unavailable",
        "Review requires a frozen read-only repository tool catalog.",
        details={"submission_unknown": False},
    )


if __name__ == '__main__':
    pass
