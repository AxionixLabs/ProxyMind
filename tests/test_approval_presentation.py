# -*- coding: utf-8 -*-

from mind_app.approval.presentation import (
    ApplyPatchApprovalPresentation,
    ExecApprovalPresentation,
    build_approval_presentation,
    ensure_approval_presentation,
)


def test_command_payload_is_normalized_to_exec_presentation() -> None:
    presentation = build_approval_presentation({
        "id": "approval-command",
        "tool": "exec_command",
        "arguments": {
            "command": ["pwsh", "-Command", "Get-Date"],
        },
    })

    assert isinstance(presentation, ExecApprovalPresentation)
    assert presentation.context.kind == "exec"
    assert presentation.context.approval_id == "approval-command"
    assert presentation.commands == (("pwsh", "-Command", "Get-Date"),)
    assert presentation.context.prompt == (
        "Would you like to run the following command?"
    )
    assert ensure_approval_presentation(presentation) is presentation


def test_patch_payload_is_normalized_with_structured_preview() -> None:
    presentation = build_approval_presentation({
        "id": "approval-patch",
        "tool": "apply_patch",
        "arguments": {"patch": "*** Begin Patch"},
        "patch": "*** Begin Patch",
        "preview": {
            "delta": {
                "changes": [{
                    "path": "src/app.py",
                    "action": "modify",
                    "old_content": "old\n",
                    "new_content": "new\n",
                    "source_path": None,
                    "hunks": [{"lines": [
                        {
                            "kind": "remove",
                            "text": "old",
                            "old_line": 1,
                            "new_line": None,
                        },
                        {
                            "kind": "add",
                            "text": "new",
                            "old_line": None,
                            "new_line": 1,
                        },
                    ]}],
                }],
            },
            "files": [{"path": "src/app.py"}],
        },
    })

    assert isinstance(presentation, ApplyPatchApprovalPresentation)
    assert presentation.context.kind == "apply_patch"
    assert presentation.patch == "*** Begin Patch"
    assert presentation.patch_view is not None
    assert presentation.patch_view.files[0].new_path == "src/app.py"
    assert presentation.patch_view.files[0].added == 1
    assert presentation.patch_view.files[0].removed == 1


def test_explicit_kind_controls_presentation_title_without_tool_name() -> None:
    presentation = build_approval_presentation({
        "id": "approval-permissions",
        "kind": "permissions",
    })

    assert presentation.context.kind == "permissions"
    assert presentation.context.prompt == (
        "Would you like to grant these permissions?"
    )
