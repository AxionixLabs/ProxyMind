# -*- coding: utf-8 -*-

import hashlib

import pytest

from protocol.schema.review import (
    REVIEW_EMPTY_WORKSPACE_REVISION,
    ClientReviewWorkspace,
    MindReviewRequest,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewExecutionOptions,
    ReviewUncommittedTarget,
    ReviewWorkspaceFile,
    canonical_review_digest,
    parse_mind_review_request,
    parse_review_output,
    parse_review_response,
    parse_review_target,
    parse_review_workspace,
)
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
    ReviewStartedEvent,
    parse_stream_event,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REQUEST_ID = "review_request_01"
FULL_SHA = "a" * 40


def _tool(name: str = "read_file") -> dict:
    """构造服务端严格契约接受的只读工具。"""
    return {
        "name": name,
        "description": f"Read repository data with {name}.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    }


def _execution() -> ReviewExecutionOptions:
    """构造最小只读 Review execution。"""
    return ReviewExecutionOptions(
        llm_conf={"primary": {"model": "test"}},
        tools=(_tool(),),
        metadata={"cid": CID, "sid": SID},
    )


def _request(
    *,
    target=None,
    workspace=None,
) -> MindReviewRequest:
    """构造严格 Review 请求。"""
    return MindReviewRequest(
        request_id=REQUEST_ID,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target or ReviewCustomTarget("Review boundaries."),
        workspace=workspace or ClientReviewWorkspace.create(),
        execution=_execution(),
    )


def _output() -> dict:
    """构造合法 ReviewOutput wire 对象。"""
    return {
        "findings": [{
            "title": "Reject stale event",
            "body": "The event can overwrite the active projection.",
            "confidence_score": 0.95,
            "priority": 1,
            "code_location": {
                "path": "agent/runtime.py",
                "line_range": {"start": 10, "end": 12},
            },
        }],
        "overall_correctness": "incorrect",
        "overall_explanation": "One lifecycle issue remains.",
        "overall_confidence_score": 0.9,
    }


def _event(event_type: str, **fields) -> dict:
    """构造带 Canonical Item 投影的 Review 事件。"""
    status = {
        "review.started": "in_progress",
        "review.completed": "completed",
        "review.failed": "failed",
        "review.cancelled": "cancelled",
        "review.reconciliation_required": "reconciliation_required",
    }[event_type]
    return {
        "type": event_type,
        "proto": "mind.chat",
        "cid": CID,
        "sid": SID,
        "turn_id": TURN_ID,
        "event_seq": 2,
        "presentation_epoch": 1,
        "item_id": f"{TURN_ID}:review",
        "item_kind": "review",
        "item_status": status,
        "review_item_id": f"{TURN_ID}:review",
        "status": status,
        **fields,
    }


def test_empty_workspace_revision_matches_server_canonical_value() -> None:
    workspace = ClientReviewWorkspace.create()

    assert workspace.revision == REVIEW_EMPTY_WORKSPACE_REVISION
    assert canonical_review_digest({"patch": "", "files": []}) == (
        REVIEW_EMPTY_WORKSPACE_REVISION.removeprefix("sha256:")
    )


def test_workspace_normalizes_paths_and_round_trips_strict_payload() -> None:
    content = "print('ready')\n"
    workspace_file = ReviewWorkspaceFile(
        path="agent\\main.py",
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    workspace = ClientReviewWorkspace.create(
        patch="diff --git a/agent/main.py b/agent/main.py\n",
        files=(workspace_file,),
    )

    parsed = parse_review_workspace(workspace.request_payload())

    assert parsed == workspace
    assert workspace_file.path == "agent/main.py"


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        (ReviewUncommittedTarget(), {"type": "uncommitted_changes"}),
        (
            ReviewBaseBranchTarget(" main "),
            {
                "type": "base_branch",
                "branch": "main",
                "merge_base_sha": None,
            },
        ),
        (
            ReviewCommitTarget("A" * 40, " title "),
            {"type": "commit", "sha": FULL_SHA, "title": "title"},
        ),
        (
            ReviewCustomTarget("  first\nsecond  "),
            {"type": "custom", "instructions": "first\nsecond"},
        ),
    ),
)
def test_review_targets_round_trip(target, expected) -> None:
    assert target.request_payload() == expected
    assert parse_review_target(expected) == target


def test_protocol_parsers_apply_formal_server_defaults() -> None:
    commit = parse_review_target({"type": "commit", "sha": FULL_SHA})
    workspace = parse_review_workspace({
        "source": "client",
        "revision": REVIEW_EMPTY_WORKSPACE_REVISION,
    })
    output_payload = _output()
    del output_payload["findings"]
    output = parse_review_output(output_payload)

    assert commit == ReviewCommitTarget(FULL_SHA)
    assert workspace == ClientReviewWorkspace.create()
    assert output.findings == ()


@pytest.mark.parametrize(
    "branch",
    (".", "../main", "/main", "main..next", "main.lock", "main branch"),
)
def test_base_branch_rejects_invalid_git_refs(branch: str) -> None:
    with pytest.raises(ValueError, match="branch is invalid"):
        ReviewBaseBranchTarget(branch)


@pytest.mark.parametrize(
    "target",
    (
        ReviewUncommittedTarget(),
        ReviewBaseBranchTarget("main", FULL_SHA),
        ReviewCommitTarget(FULL_SHA),
        ReviewCustomTarget("Review boundaries."),
    ),
)
def test_all_targets_accept_canonical_empty_workspace(target) -> None:
    assert _request(target=target).workspace.revision == (
        REVIEW_EMPTY_WORKSPACE_REVISION
    )


def test_request_round_trip_keeps_only_read_only_execution_fields() -> None:
    request = _request()
    payload = request.request_payload()

    assert parse_mind_review_request(payload) == request
    assert payload["execution"] == {
        "llm_conf": {"primary": {"model": "test"}},
        "additional_context": [],
        "system_message": "",
        "attachments": None,
        "streaming": False,
        "tools": [_tool()],
        "hosted_tools": None,
        "skills": None,
        "sandbox_mode": "read-only",
        "metadata": {"cid": CID, "sid": SID},
    }


def test_review_tools_must_explicitly_declare_read_only_hint() -> None:
    with pytest.raises(ValueError, match="readOnlyHint"):
        ReviewExecutionOptions(
            llm_conf={"primary": {}},
            tools=({**_tool(), "annotations": {}},),
        )

    execution = ReviewExecutionOptions(
        llm_conf={"primary": {}},
        tools=(_tool(),),
    )
    assert execution.request_payload()["tools"] == [_tool()]


def test_review_tools_reject_forbidden_names_and_unknown_wire_fields() -> None:
    with pytest.raises(ValueError, match="not permitted"):
        ReviewExecutionOptions(
            llm_conf={"primary": {}},
            tools=(_tool("shell_command"),),
        )

    with pytest.raises(ValueError, match="unknown fields"):
        ReviewExecutionOptions(
            llm_conf={"primary": {}},
            tools=({**_tool(), "meta": {"client_builtin": True}},),
        )


def test_review_llm_conf_rejects_local_provider_and_host_fields() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        ReviewExecutionOptions(
            llm_conf={
                "primary": {
                    "model": "test",
                    "name": "OpenAI",
                    "kind": "openai_compatible",
                    "enabled": True,
                },
            },
            tools=(_tool(),),
        )


def test_workspace_enforces_revision_and_utf8_total_size() -> None:
    with pytest.raises(ValueError, match="revision"):
        ClientReviewWorkspace(revision="sha256:" + "0" * 64)

    content = "界" * 1_000_000
    workspace_file = ReviewWorkspaceFile.from_content("large.txt", content)
    with pytest.raises(ValueError, match="total byte limit"):
        ClientReviewWorkspace.create(
            patch="x" * 1_000_001,
            files=(workspace_file,),
        )


@pytest.mark.parametrize(
    ("parser", "payload"),
    (
        (
            parse_review_target,
            {"type": "custom", "instructions": "review", "legacy": True},
        ),
        (
            parse_review_workspace,
            {
                "source": "client",
                "revision": REVIEW_EMPTY_WORKSPACE_REVISION,
                "patch": "",
                "files": [],
                "workspace_path": ".",
            },
        ),
        (
            parse_review_output,
            {**_output(), "summary": "legacy"},
        ),
    ),
)
def test_review_protocol_objects_reject_unknown_fields(parser, payload) -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        parser(payload)


def test_review_output_is_strictly_typed_and_normalized() -> None:
    output = parse_review_output(_output())

    assert output.overall_explanation == "One lifecycle issue remains."
    assert output.findings[0].code_location.path == "agent/runtime.py"
    assert output.payload() == _output()

    invalid = _output()
    invalid["findings"][0]["priority"] = True
    with pytest.raises(TypeError, match="priority"):
        parse_review_output(invalid)


@pytest.mark.parametrize("status", ("accepted", "idempotent"))
def test_review_receipt_accepts_only_formal_success_status(status: str) -> None:
    receipt = parse_review_response({
        "ok": True,
        "data": {
            "request_id": REQUEST_ID,
            "status": status,
            "cid": CID,
            "sid": SID,
            "turn_id": TURN_ID,
            "review_session": {"cid": CID, "sid": SID},
            "delivery": "inline",
        },
    })

    assert receipt.status == status
    assert receipt.review_session.sid == SID


def test_review_events_are_strictly_typed() -> None:
    started = parse_stream_event(_event(
        "review.started",
        target={"type": "uncommitted_changes"},
        workspace_revision="sha256:" + "a" * 64,
        prompt_version="mind-review/1",
    ))
    completed = parse_stream_event(_event("review.completed", output=_output()))
    failed = parse_stream_event(_event("review.failed", error="provider failed"))
    cancelled = parse_stream_event(_event("review.cancelled", reason="interrupted"))
    reconciliation = parse_stream_event(_event(
        "review.reconciliation_required",
        effect_id="effect_review_01",
        error="effect result is unknown",
    ))

    assert isinstance(started, ReviewStartedEvent)
    assert isinstance(completed, ReviewCompletedEvent)
    assert completed.output.findings[0].priority == 1
    assert isinstance(failed, ReviewFailedEvent)
    assert failed.output_preview == ""
    assert isinstance(cancelled, ReviewCancelledEvent)
    assert isinstance(reconciliation, ReviewReconciliationRequiredEvent)


@pytest.mark.parametrize(
    "mutation",
    (
        {"item_id": "another-review"},
        {"status": "completed"},
        {"prompt_version": "mind-review/2"},
        {"legacy": True},
    ),
)
def test_review_started_rejects_identity_status_version_and_unknown_fields(
    mutation: dict,
) -> None:
    payload = _event(
        "review.started",
        target={"type": "uncommitted_changes"},
        workspace_revision="sha256:" + "a" * 64,
        prompt_version="mind-review/1",
    )
    payload.update(mutation)

    with pytest.raises(ValueError):
        parse_stream_event(payload)
