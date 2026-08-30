import os

from infrastructure.platform.shell_runtime import ShellRuntimeResolver


def test_requested_shell_overrides_platform_and_environment_defaults() -> None:
    runtime = ShellRuntimeResolver.resolve(
        env={"SHELL": "/bin/zsh", "PATH": ""},
        shell="C:/Tools/pwsh.exe",
    )

    assert runtime.name == "pwsh"
    assert runtime.syntax == "powershell"
    assert runtime.executable == "C:/Tools/pwsh.exe"
    assert runtime.prefix == [
        "C:/Tools/pwsh.exe",
        "-NoProfile",
        "-Command",
    ]


def test_default_shell_resolution_is_unchanged_without_request() -> None:
    runtime = ShellRuntimeResolver.resolve(
        env={"SHELL": "/bin/zsh", "PATH": ""},
    )

    if os.name == "nt":
        assert runtime.name == "cmd"
        assert runtime.syntax == "cmd"
        assert runtime.prefix[-1] == "/c"
    else:
        assert runtime.name == "zsh"
        assert runtime.syntax == "posix"
        assert runtime.prefix[-1] == "-lc"
