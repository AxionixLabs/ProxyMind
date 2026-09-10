import os
import sys
import textwrap
from pathlib import Path

import pytest

from tests.pty import TerminalSize
from tests.pty import spawn_terminal


pytestmark = [
    pytest.mark.pty_acceptance,
    pytest.mark.skipif(os.name != "nt", reason="Windows console inheritance"),
]


@pytest.mark.parametrize("inherit_console", (False, True))
def test_sidecar_cannot_write_around_captured_stderr_to_parent_console(
    tmp_path: Path,
    inherit_console: bool,
) -> None:
    sidecar = tmp_path / "sidecar.py"
    sidecar.write_text(textwrap.dedent('''\
        import sys

        try:
            with open("CONOUT$", "w") as console:
                console.write("CONSOLE_LEAK_SENTINEL\\n")
        except OSError:
            pass
        print("CAPTURED_STDERR_SENTINEL", file=sys.stderr, flush=True)
        sys.exit(1)
    '''), encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f'''\
        import asyncio
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from infrastructure.platform.sandbox import SandboxClient
        from infrastructure.platform.sandbox import SandboxUnavailable

        async def main():
            original_spawn = asyncio.create_subprocess_exec

            async def spawn(executable, **kwargs):
                if {inherit_console!r}:
                    kwargs["creationflags"] = 0
                return await original_spawn(executable, {str(sidecar)!r}, **kwargs)

            client = SandboxClient(
                workspace_root=Path.cwd(), executable=Path(sys.executable), platform="win32",
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
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root)
    with spawn_terminal(
        [sys.executable, "-X", "utf8", str(driver)],
        cwd=root,
        env=environment,
        size=TerminalSize(rows=20, columns=120),
    ) as terminal:
        terminal.wait_for_screen_text("RESULT stderr captured", timeout=15)
        assert terminal.wait_for_exit(timeout=10) == 0
        output = terminal.session.output_text()

    assert ("CONSOLE_LEAK_SENTINEL" in output) is inherit_console
