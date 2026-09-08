import pytest

from infrastructure.platform.sandbox_denials import classify_sandbox_denial


pytestmark = pytest.mark.runtime_p0


@pytest.mark.parametrize(
    ("backend", "runtime_name", "command", "stderr", "evidence_code"),
    (
        (
            "windows-sidecar",
            "pwsh",
            "Get-Content -LiteralPath 'C:\\protected.txt'",
            "Get-Content: Access to the path 'C:\\protected.txt' is denied.",
            "windows_powershell_path_access_denied",
        ),
        (
            "windows-sidecar",
            "powershell",
            "Remove-Item -LiteralPath 'C:\\protected.txt'",
            "Remove-Item: Access to the path 'C:\\protected.txt' is denied.",
            "windows_powershell_path_access_denied",
        ),
        (
            "macos-sidecar",
            "sh",
            "touch .git/blocked.txt",
            "touch: .git/blocked.txt: Operation not permitted",
            "macos_shell_operation_not_permitted",
        ),
        (
            "macos-sidecar",
            "zsh",
            "printf blocked > .git/blocked.txt",
            "zsh: .git/blocked.txt: operation not permitted",
            "macos_shell_operation_not_permitted",
        ),
    ),
)
def test_post_spawn_policy_denial_requires_controlled_platform_evidence(
    backend: str,
    runtime_name: str,
    command: str,
    stderr: str,
    evidence_code: str,
) -> None:
    evidence = classify_sandbox_denial(
        backend=backend,
        runtime_name=runtime_name,
        command=command,
        exit_code=1,
        stderr=stderr,
    )

    assert evidence is not None
    assert evidence.evidence_source == "inferred_output"
    assert evidence.evidence_code == evidence_code
    assert evidence.stage == "execution"


@pytest.mark.parametrize(
    ("backend", "runtime_name", "command", "exit_code", "stderr", "flags"),
    (
        (
            "windows-sidecar",
            "pwsh",
            "python - <<'PY'",
            1,
            "ParserError: Missing file specification after redirection operator.",
            {},
        ),
        (
            "windows-sidecar",
            "pwsh",
            "missing-command",
            1,
            "missing-command: The term 'missing-command' is not recognized.",
            {},
        ),
        (
            "windows-sidecar",
            "pwsh",
            "Write-Error 'sandbox access denied'",
            1,
            "Write-Error: sandbox access denied",
            {},
        ),
        (
            "windows-sidecar",
            "pwsh",
            "Write-Output ordinary",
            1,
            "Get-Content: Access to the path 'C:\\protected.txt' is denied.",
            {},
        ),
        (
            "local",
            "pwsh",
            "Get-Content -LiteralPath 'C:\\protected.txt'",
            1,
            "Get-Content: Access to the path 'C:\\protected.txt' is denied.",
            {},
        ),
        (
            "macos-sidecar",
            "sh",
            "missing-command",
            127,
            "sh: missing-command: not found",
            {},
        ),
        (
            "macos-sidecar",
            "sh",
            "printf ok",
            0,
            "sh: target: Operation not permitted",
            {},
        ),
        (
            "macos-sidecar",
            "sh",
            "touch protected",
            1,
            "touch: protected: Operation not permitted",
            {"timed_out": True},
        ),
        (
            "macos-sidecar",
            "sh",
            "touch protected",
            1,
            "touch: protected: Operation not permitted",
            {"execution_outcome_unknown": True},
        ),
    ),
)
def test_post_spawn_policy_denial_rejects_false_positive_matrix(
    backend: str,
    runtime_name: str,
    command: str,
    exit_code: int,
    stderr: str,
    flags: dict[str, bool],
) -> None:
    assert classify_sandbox_denial(
        backend=backend,
        runtime_name=runtime_name,
        command=command,
        exit_code=exit_code,
        stderr=stderr,
        **flags,
    ) is None


if __name__ == '__main__':
    pass
