# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext,
)
from mind_app.runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
    resolve_turn_hook_scope,
)
from mind_core.permissions import PermissionSettings

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


async def prepare_root_turn(
    controller: "Mind",
    *,
    message: str,
    title: str,
    source: str,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    metadata: Mapping[str, typing.Any],
    attachments: typing.Iterable[Mapping[str, typing.Any]],
    extras: Mapping[str, typing.Any] | None,
    turn_id: str | None,
) -> TurnExecution:
    """固定根轮次的会话身份、输入快照和执行上下文。"""
    supplied_metadata = dict(metadata)
    conversation_turn = await controller.begin_conversation_turn(
        cid=supplied_metadata.get("cid"),
        sid=supplied_metadata.get("sid"),
        title=title,
        source=source,
    )
    canonical_metadata = {
        **supplied_metadata,
        **conversation_turn.metadata(),
    }
    sid = canonical_metadata["sid"]
    context = TurnContext.create(
        agent=AgentContext.root(sid),
        cid=canonical_metadata["cid"],
        sid=sid,
        source=source,
        pref_config=pref_config,
        cwd=controller.history_workspace,
        permissions=permissions,
        permission_grants=getattr(controller, "permission_grants", None),
        output_record_path=str(controller.report.output_record_path or ""),
        transcript_path=controller.transcripts.path_for_session(sid),
        turn_id=turn_id,
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    return TurnExecution(
        context=context,
        message=message,
        hook_scope=resolve_turn_hook_scope(controller, context),
        metadata=canonical_metadata,
        additional_context=conversation_turn.additional_context,
        system_message=conversation_turn.system_message,
        input_payload=build_turn_input_payload(
            message,
            attachments=attachments,
            extras=extras,
        ),
    )


if __name__ == '__main__':
    pass
