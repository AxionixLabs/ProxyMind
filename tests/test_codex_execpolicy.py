# -*- coding: utf-8 -*-

from mind_app.native_coding.exec.exec_policy import (
    ExecApprovalRequirement,
    ExecPolicyManager,
    commands_for_exec_policy,
    effective_sandbox_mode,
    normalize_sandbox_permission,
    render_decision_for_unmatched_command,
)
from mind_app.native_coding.exec.execpolicy import Decision, PolicyParser


def test_rules_parser_uses_strictest_matching_decision() -> None:
    policy = PolicyParser.new(
        """
prefix_rule(pattern=["git", "push"], decision="prompt")
prefix_rule(pattern=["rm"], decision="forbidden")
prefix_rule(pattern=["cargo"], decision="allow")
network_rule(host="api.github.com", protocol="https", decision="allow")
"""
    ).build()

    assert policy.check(["git", "push"]).decision is Decision.Prompt
    assert policy.check(["rm", "-rf", "tmp"]).decision is Decision.Forbidden
    assert policy.check(["curl", "https://api.github.com/releases"]).decision is Decision.Allow


def test_unmatched_commands_follow_codex_fallback() -> None:
    assert render_decision_for_unmatched_command(
        ["git", "status"],
        approval_policy="on-request",
        sandbox_mode="danger-full-access",
    ) is Decision.Allow
    assert render_decision_for_unmatched_command(
        ["adb", "devices"],
        approval_policy="on-request",
        sandbox_mode="read-only",
    ) is Decision.Prompt
    assert render_decision_for_unmatched_command(
        ["adb", "devices"],
        approval_policy="on-request",
        sandbox_mode="danger-full-access",
    ) is Decision.Allow
    assert render_decision_for_unmatched_command(
        ["rm", "-rf", "tmp"], approval_policy="on-request"
    ) is Decision.Prompt
    assert render_decision_for_unmatched_command(
        ["rm", "-rf", "tmp"], approval_policy="never"
    ) is Decision.Forbidden
    assert render_decision_for_unmatched_command(
        ["git", "status"], approval_policy="untrusted"
    ) is Decision.Prompt


def test_shell_wrapper_commands_are_exposed_to_policy() -> None:
    assert commands_for_exec_policy(["bash", "-lc", "git status && rg foo"]) == [
        ["git", "status"],
        ["rg", "foo"],
    ]

    assert ["rm", "-rf", "tmp"] in commands_for_exec_policy(
        ["bash", "-lc", "if true; then rm -rf tmp; fi"]
    )


def test_manager_loads_workspace_rules(tmp_path) -> None:
    rules = tmp_path / ".mind" / "rules"
    rules.mkdir(parents=True)
    (rules / "local.rules").write_text(
        'prefix_rule(pattern=["adb"], decision="allow")\n',
        encoding="utf-8",
    )

    manager = ExecPolicyManager(workspace_root=tmp_path)
    assert manager.decide(["adb", "shell", "id"]).decision is Decision.Allow


def test_allowed_segment_does_not_hide_dangerous_unmatched_segment(tmp_path) -> None:
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        policy=PolicyParser.new(
            'prefix_rule(pattern=["git"], decision="allow")\n'
        ).build(),
    )

    evaluation = manager.decide(
        "git status && rm -rf tmp",
        approval_policy="on-request",
    )

    assert evaluation.decision is Decision.Prompt
    assert tuple(
        getattr(match, "decision", None)
        for match in evaluation.matched_rules
    ) == (Decision.Allow,)


def test_forbidden_segment_is_stricter_than_allowed_segment(tmp_path) -> None:
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        policy=PolicyParser.new(
            'prefix_rule(pattern=["git"], decision="allow")\n'
            'prefix_rule(pattern=["rm"], decision="forbidden")\n'
        ).build(),
    )

    assert manager.decide(
        "git status && rm -rf tmp",
        approval_policy="on-request",
    ).decision is Decision.Forbidden


def test_session_approval_is_exact_and_does_not_override_forbidden(tmp_path) -> None:
    policy = PolicyParser.new(
        'prefix_rule(pattern=["blocked"], decision="forbidden")\n'
    ).build()
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        policy=policy,
        writable_rules_path=tmp_path / ".mind" / "rules" / "default.rules",
    )

    manager.add_approval_for_session("rm -rf build", cwd=tmp_path)
    manager.add_approval_for_session("blocked command", cwd=tmp_path)

    assert manager.decide("rm -rf build", cwd=tmp_path).decision is Decision.Allow
    assert manager.decide("rm -rf other", cwd=tmp_path).decision is Decision.Prompt
    assert manager.decide("blocked command", cwd=tmp_path).decision is Decision.Forbidden


def test_persistent_amendment_updates_memory_and_local_rules(tmp_path) -> None:
    rules_path = tmp_path / ".mind" / "rules" / "default.rules"
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        writable_rules_path=rules_path,
    )
    proposal = manager.proposed_execpolicy_amendment(
        "rm -rf build",
        amendment_id="rule-1",
    )

    assert proposal == {
        "id": "rule-1",
        "command_prefix": ["rm", "-rf"],
        "display": "rm -rf",
    }
    manager.persist_execpolicy_amendment(proposal)
    manager.persist_execpolicy_amendment(proposal)

    assert manager.decide("rm -rf other").decision is Decision.Allow
    assert rules_path.read_text(encoding="utf-8") == (
        'prefix_rule(pattern=["rm", "-rf"], decision="allow")\n'
    )

    reloaded = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(rules_path,),
        writable_rules_path=rules_path,
    )
    assert reloaded.decide("rm -rf cache").decision is Decision.Allow


def test_complex_shell_command_has_no_persistent_prefix_proposal(tmp_path) -> None:
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        writable_rules_path=tmp_path / ".mind" / "rules" / "default.rules",
    )

    assert manager.proposed_execpolicy_amendment(
        "git status && rm -rf build",
        amendment_id="rule-1",
    ) is None


def test_requirement_matches_codex_three_state_amendment_and_bypass(tmp_path) -> None:
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        policy=PolicyParser.new(
            'prefix_rule(pattern=["python3"], decision="allow")\n'
        ).build(),
    )

    allowed = manager.create_exec_approval_requirement_for_command(
        ["python3", "-c", "print(1)"],
        approval_policy="on-request",
        amendment_id="allow-1",
    )
    assert allowed == ExecApprovalRequirement.skip(bypass_sandbox=True)

    prompted = manager.create_exec_approval_requirement_for_command(
        ["cargo", "build"],
        approval_policy="untrusted",
        amendment_id="prompt-1",
    )
    assert prompted.state == "needs_approval"
    assert prompted.proposed_execpolicy_amendment is not None
    assert prompted.proposed_execpolicy_amendment.command_prefix == (
        "cargo", "build"
    )

    readonly_adb = manager.create_exec_approval_requirement_for_command(
        "adb devices",
        approval_policy="on-request",
        sandbox_mode="read-only",
    )
    assert readonly_adb.state == "needs_approval"


def test_require_escalated_requests_host_approval_and_is_session_scoped(tmp_path) -> None:
    manager = ExecPolicyManager(workspace_root=tmp_path, rules_paths=())

    requirement = manager.create_exec_approval_requirement_for_command(
        "echo hello",
        approval_policy="on-request",
        sandbox_mode="workspace-write",
        sandbox_permissions="require_escalated",
    )
    assert requirement.state == "needs_approval"
    assert requirement.reason == "command requires approval"

    forbidden = manager.create_exec_approval_requirement_for_command(
        "Start-Process https://example.com",
        approval_policy="never",
        sandbox_mode="workspace-write",
        sandbox_permissions="require_escalated",
    )
    assert forbidden.state == "forbidden"

    full_access = manager.create_exec_approval_requirement_for_command(
        "echo hello",
        approval_policy="on-request",
        sandbox_mode="danger-full-access",
        sandbox_permissions="require_escalated",
    )
    assert full_access.state == "skip"

    manager.add_approval_for_session(
        "rm -rf build",
        cwd=tmp_path,
        sandbox_permissions="require_escalated",
    )
    assert manager.create_exec_approval_requirement_for_command(
        "rm -rf build",
        cwd=tmp_path,
        sandbox_mode="workspace-write",
        sandbox_permissions="require_escalated",
    ).state == "skip"
    assert manager.create_exec_approval_requirement_for_command(
        "rm -rf build",
        cwd=tmp_path,
        sandbox_mode="workspace-write",
        sandbox_permissions="use_default",
    ).state == "needs_approval"


def test_sandbox_permission_helpers_validate_and_select_host_mode() -> None:
    assert normalize_sandbox_permission(None) == "use_default"
    assert normalize_sandbox_permission("REQUIRE_ESCALATED") == "require_escalated"
    assert effective_sandbox_mode("workspace-write", "require_escalated") == (
        "danger-full-access"
    )
    assert effective_sandbox_mode("workspace-write", "use_default") == (
        "workspace-write"
    )

    try:
        normalize_sandbox_permission("with_additional_permissions")
    except ValueError as error:
        assert "sandbox_permissions" in str(error)
    else:
        raise AssertionError("invalid sandbox permission must be rejected")


def test_policy_prompt_never_gets_no_persistent_amendment(tmp_path) -> None:
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        policy=PolicyParser.new(
            'prefix_rule(pattern=["git", "push"], decision="prompt")\n'
        ).build(),
    )

    requirement = manager.create_exec_approval_requirement_for_command(
        ["git", "push"],
        approval_policy="on-request",
        amendment_id="rule-1",
    )
    assert requirement.state == "needs_approval"
    assert requirement.proposed_execpolicy_amendment is None


def test_host_executable_rule_uses_codex_name_and_paths_shape() -> None:
    policy = PolicyParser.new(
        'host_executable(name="python", paths=["C:/Tools/python.exe"])\n'
        'prefix_rule(pattern=["python"], decision="allow")\n'
    ).build()
    manager = ExecPolicyManager(
        rules_paths=(),
        policy=policy,
    )

    assert manager.decide(["C:/Tools/python.exe", "-V"]).decision is Decision.Allow
    assert manager.decide(
        ["C:/Other/python.exe", "-V"],
        approval_policy="untrusted",
    ).decision is Decision.Prompt
