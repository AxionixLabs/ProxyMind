# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from collections.abc import Mapping

from agent.domain.tool_policy import (
    REVIEW_TOOL_NAMES,
    filter_mode_tools,
)
from agent.ports import (
    McpSessionPort,
    TurnExecutionRuntimePort,
)
from agent.protocol import (
    ReviewStreamRequest,
    SubmitReviewCommand,
)
from agent.protocol.json_value import JsonValue as FrozenJsonValue
from protocol.client.payload import request_llm_conf
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)
from protocol.schema.identifiers import new_request_id
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewRequest,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewExecutionOptions,
    ReviewTarget,
    ReviewUncommittedTarget,
    parse_review_target,
)


def create_review_command(
    *,
    local_session_id: str,
    cid: str,
    sid: str,
    turn_id: str,
    target: ReviewTarget,
    workspace: ClientReviewWorkspace,
    pref_config: Mapping[str, JsonValue],
    environment_snapshot: Mapping[str, FrozenJsonValue] | None,
    tools: tuple[JsonObject, ...],
    session_mode: typing.Literal["create", "existing"],
) -> SubmitReviewCommand:
    """从类型化本地输入创建完整冻结且可持久化的 Review 命令。"""
    request = MindReviewRequest(
        request_id=new_request_id("review"),
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        session_mode=session_mode,
        target=target,
        workspace=workspace,
        execution=ReviewExecutionOptions(
            llm_conf=request_llm_conf(dict(pref_config)),
            tools=tools,
            metadata={"cid": cid, "sid": sid},
        ),
    )
    return SubmitReviewCommand.create(
        session_id=local_session_id,
        request=ReviewStreamRequest.from_dict(request.request_payload()),
        environment_snapshot=environment_snapshot,
        trace_context={
            "remote_turn": {
                "cid": cid,
                "sid": sid,
                "turn_id": turn_id,
            },
        },
    )


async def discover_review_tools(
    runtime: TurnExecutionRuntimePort,
    pref_config: dict[str, JsonValue],
) -> tuple[JsonObject, ...]:
    """在短期 MCP 会话中冻结 Review 可用的本地命令工具。"""

    async def freeze_catalog(
        session: McpSessionPort,
        tools: list[JsonObject],
    ) -> tuple[JsonObject, ...]:
        """把当前工具目录收窄为可持久化的 Review wire 描述。"""
        del session
        return review_wire_tools(tools)

    result = await runtime.with_mcp_session(pref_config, freeze_catalog)
    if not isinstance(result, tuple):
        raise TypeError("review tool discovery returned an invalid catalog")
    return result


def review_wire_tools(
    tools: list[JsonObject],
) -> tuple[JsonObject, ...]:
    """从真实工具目录冻结在 Review 只读沙箱中执行的命令工具。"""
    visible = filter_mode_tools("review", tools)
    by_name = {
        str(tool.get("name") or "").strip(): tool
        for tool in visible
    }
    missing = REVIEW_TOOL_NAMES.difference(by_name)
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(f"review tools are unavailable: {names}")

    frozen: list[JsonObject] = []
    for name in sorted(REVIEW_TOOL_NAMES):
        tool = by_name.get(name)
        if tool is None:
            continue
        description = tool.get("description")
        input_schema = tool.get("inputSchema")
        if not isinstance(description, str) or not isinstance(input_schema, dict):
            raise RuntimeError(f"review tool definition is invalid: {name}")
        frozen.append({
            "name": name,
            "description": description,
            "inputSchema": dict(input_schema),
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        })
    return tuple(frozen)


def review_target_hint(target: ReviewTarget) -> str:
    """返回提交准备和 Review 启动态共享的目标摘要。"""
    if isinstance(target, ReviewUncommittedTarget):
        return "current changes"
    if isinstance(target, ReviewBaseBranchTarget):
        return f"changes against '{target.branch}'"
    if isinstance(target, ReviewCommitTarget):
        title = f": {target.title}" if target.title else ""
        return f"commit {target.sha[:7]}{title}"
    if isinstance(target, ReviewCustomTarget):
        return target.instructions
    raise TypeError("unsupported Review target")


def review_request_hint(request: ReviewStreamRequest) -> str:
    """从已校验的冻结请求恢复 Review 目标摘要。"""
    if not isinstance(request, ReviewStreamRequest):
        raise TypeError("review request is required")
    target = request.to_dict()["target"]
    return review_target_hint(parse_review_target(target))


if __name__ == '__main__':
    pass
