import os
import sys
import textwrap
from pathlib import Path

import pytest

from tests.pty import (
    TerminalSize,
    spawn_terminal,
)


pytestmark = [
    pytest.mark.pty_acceptance,
    pytest.mark.skipif(os.name not in {"nt", "posix"}, reason="requires a native terminal"),
]


@pytest.mark.parametrize("inherit_console", (False, True), ids=("isolated", "inherited"))
def test_sidecar_cannot_write_around_captured_stderr_to_parent_console(
    tmp_path: Path,
    repository_root: Path,
    inherit_console: bool,
) -> None:
    sidecar = tmp_path / "sidecar.py"
    sidecar.write_text(textwrap.dedent('''\
        import errno
        import os
        import sys

        try:
            with open("CONOUT$" if os.name == "nt" else "/dev/tty", "w") as console:
                console.write("CONSOLE_LEAK_SENTINEL\\n")
        except OSError as error:
            if os.name == "posix" and error.errno != errno.ENXIO:
                raise
        print("CAPTURED_STDERR_SENTINEL", file=sys.stderr, flush=True)
        sys.exit(1)
    '''), encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f'''\
        import asyncio
        import os
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from infrastructure.platform.sandbox import (
            SandboxClient,
            SandboxUnavailable,
        )

        async def main():
            """在父控制终端内验证生产客户端的错误捕获。"""
            assert os.isatty(0) and os.isatty(1) and os.isatty(2)
            original_spawn = asyncio.create_subprocess_exec

            async def spawn(executable, **kwargs):
                """注入可执行探针，并按对照场景恢复父终端继承。"""
                if {inherit_console!r}:
                    kwargs["creationflags"] = 0
                    kwargs["start_new_session"] = False
                return await original_spawn(executable, {str(sidecar)!r}, **kwargs)

            client = SandboxClient(
                workspace_root=Path.cwd(), executable=Path(sys.executable), platform=sys.platform,
            )
            try:
                with patch("infrastructure.platform.sandbox.asyncio.create_subprocess_exec", new=spawn):
                    await client.ensure_started()
            except SandboxUnavailable as error:
                assert "CAPTURED_STDERR_SENTINEL" in error.detail, error.detail
                print("RESULT stderr captured", flush=True)
            else:
                raise AssertionError("sidecar failure was lost")
            finally:
                await client.close()

        asyncio.run(main())
    '''), encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repository_root)
    with spawn_terminal(
        [sys.executable, "-X", "utf8", str(driver)],
        cwd=repository_root,
        env=environment,
        size=TerminalSize(rows=20, columns=120),
    ) as terminal:
        terminal.wait_for_screen_text("RESULT stderr captured", timeout=15)
        assert terminal.wait_for_exit(timeout=10) == 0
        output = terminal.session.output_text()

    assert ("CONSOLE_LEAK_SENTINEL" in output) is inherit_console
    assert "CAPTURED_STDERR_SENTINEL" not in output
