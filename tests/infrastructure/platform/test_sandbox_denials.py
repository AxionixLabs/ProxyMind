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
            "windows-sidecar",
            "pwsh",
            "$ErrorActionPreference='Stop'; [IO.File]::WriteAllText('C:\\中文 空格.txt', 'value')",
            'ParentContainsErrorRecordException: Exception calling "WriteAllText" '
            'with "2" argument(s): "Access to the path \'C:\\中文 空格.txt\' is denied."',
            "windows_powershell_path_access_denied",
        ),
        (
            "windows-sidecar",
            "pwsh",
            "$value = [System.IO.File] :: ReadAllText ('C:\\protected.txt')",
            'MethodInvocationException: Exception calling "ReadAllText" '
            'with "1" argument(s): "Access to the path \'C:\\protected.txt\' is denied."',
            "windows_powershell_path_access_denied",
        ),
        (
            "windows-sidecar",
            "pwsh",
            "# create output\n[system.io.directory]::createdirectory('C:\\protected')",
            'MethodInvocationException: Exception calling "CreateDirectory" '
            'with "1" argument(s): "Access to the path \'C:\\protected\' is denied."',
            "windows_powershell_path_access_denied",
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


@pytest.mark.parametrize(
    "command",
    (
        "Write-Error 'access denied'",
        "[IO.File]::ReadAllText('C:\\protected.txt')",
        "[Custom.File]::WriteAllText('C:\\protected.txt', 'value')",
        "Write-Output '[IO.File]::WriteAllText()'",
        'Write-Output "[IO.File]::WriteAllText()"',
        "Write-Output 'quoted ''[IO.File]::WriteAllText()'' text'",
        "Write-Output ok # [IO.File]::WriteAllText()",
        "<# [IO.File]::WriteAllText() #> Write-Output ok",
        "<# outer <# inner #> [IO.File]::WriteAllText() #> Write-Output ok",
        "`[IO.File]::WriteAllText()",
        "@'\nquote ' [IO.File]::WriteAllText() ' text\n'@",
        '@"\nquote " [IO.File]::WriteAllText() " text\n"@',
    ),
)
def test_dotnet_denial_requires_a_matching_file_call(command: str) -> None:
    evidence = classify_sandbox_denial(
        backend="windows-sidecar",
        runtime_name="pwsh",
        command=command,
        exit_code=1,
        stderr=(
            'ParentContainsErrorRecordException: Exception calling "WriteAllText" '
            'with "2" argument(s): "Access to the path \'C:\\protected.txt\' is denied."'
        ),
    )

    assert evidence is None


@pytest.mark.parametrize(
    "stderr",
    (
        "Access to the path 'C:\\protected.txt' is denied.",
        'Write-Error: Exception calling "WriteAllText" with "2" argument(s): '
        '"Access to the path \'C:\\protected.txt\' is denied."',
        'MethodInvocationException: Exception calling "WriteAllText" with "2" argument(s): '
        '"Could not find a part of the path \'C:\\protected.txt\'."',
        'MethodInvocationException: Exception calling "WriteAllText" with "2" argument(s): '
        '"The process cannot access the file because it is being used by another process."',
        "InvalidOperation: Cannot invoke method. Method invocation is supported only on core types in this language mode.",
    ),
)
def test_dotnet_file_call_does_not_classify_unrelated_failures(stderr: str) -> None:
    assert classify_sandbox_denial(
        backend="windows-sidecar",
        runtime_name="pwsh",
        command="[IO.File]::WriteAllText('C:\\protected.txt', 'value')",
        exit_code=1,
        stderr=stderr,
    ) is None


@pytest.mark.parametrize(
    ("backend", "runtime_name", "exit_code", "timed_out", "outcome_unknown"),
    (
        ("local", "pwsh", 1, False, False),
        ("windows-sidecar", "cmd", 1, False, False),
        ("windows-sidecar", "pwsh", 0, False, False),
        ("windows-sidecar", "pwsh", 1, True, False),
        ("windows-sidecar", "pwsh", 1, False, True),
    ),
)
def test_dotnet_denial_preserves_execution_gates(
    backend: str,
    runtime_name: str,
    exit_code: int,
    timed_out: bool,
    outcome_unknown: bool,
) -> None:
    assert classify_sandbox_denial(
        backend=backend,
        runtime_name=runtime_name,
        command="[IO.File]::WriteAllText('C:\\protected.txt', 'value')",
        exit_code=exit_code,
        stderr=(
            'ParentContainsErrorRecordException: Exception calling "WriteAllText" '
            'with "2" argument(s): "Access to the path \'C:\\protected.txt\' is denied."'
        ),
        timed_out=timed_out,
        execution_outcome_unknown=outcome_unknown,
    ) is None


if __name__ == '__main__':
    pass
