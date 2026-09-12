# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import subprocess
import typing
from dataclasses import dataclass
from enum import Enum

_TMUX_QUERY_TIMEOUT_SEC = 0.1


class TerminalKind(str, Enum):
    """描述当前交互终端的已知身份。"""

    WINDOWS_TERMINAL = "windows_terminal"
    JETBRAINS_JEDITERM = "jetbrains_jediterm"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    KITTY = "kitty"
    ALACRITTY = "alacritty"
    KONSOLE = "konsole"
    FOOT = "foot"
    RIO = "rio"
    WARP = "warp"
    APPLE_TERMINAL = "apple_terminal"
    GNOME_TERMINAL = "gnome_terminal"
    VSCODE = "vscode"
    ZED = "zed"
    VTE = "vte"
    TMUX = "tmux"
    ZELLIJ = "zellij"
    DUMB = "dumb"
    UNKNOWN = "unknown"


class TerminalIdentitySource(str, Enum):
    """描述终端身份结论所使用的信号来源。"""

    TERM_PROGRAM = "term_program"
    DIRECT_SIGNAL = "direct_signal"
    TMUX_CLIENT = "tmux_client"
    MULTIPLEXER = "multiplexer"
    TERM = "term"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalIdentity:
    """保存终端身份事实、外层复用器和判定来源。"""

    kind: TerminalKind
    name: str
    multiplexer: TerminalKind | None = None
    term_program: str | None = None
    version: str | None = None
    term: str | None = None
    multiplexer_version: str | None = None
    source: TerminalIdentitySource = TerminalIdentitySource.UNKNOWN
    source_variable: str | None = None

    @property
    def is_ide_terminal(self) -> bool:
        """根据已解析的宿主身份判断是否属于已知 IDE 集成终端。"""
        return self.kind in {
            TerminalKind.JETBRAINS_JEDITERM,
            TerminalKind.VSCODE,
            TerminalKind.ZED,
        }


TmuxProbe: typing.TypeAlias = typing.Callable[[], tuple[str, str] | None]


def detect_terminal_identity(
    environ: typing.Mapping[str, str] | None = None,
    *,
    tmux_probe: TmuxProbe | None = None,
) -> TerminalIdentity:
    """按固定优先级识别终端、版本和外层复用器。"""

    env = os.environ if environ is None else environ
    multiplexer, mux_version = _detect_multiplexer(env)
    program = str(env.get("TERM_PROGRAM") or "").strip()

    if multiplexer is TerminalKind.TMUX:
        queried = (tmux_probe or _query_tmux_client)()
        if queried is not None:
            term_type, term_name = queried
            kind, version = _kind_and_version_from_program(term_type)
            return TerminalIdentity(
                kind,
                term_name or term_type or "tmux",
                multiplexer=multiplexer,
                term_program=term_type or None,
                version=version,
                term=term_name or None,
                multiplexer_version=mux_version,
                source=TerminalIdentitySource.TMUX_CLIENT,
                source_variable="TMUX",
            )

    program_identity: TerminalIdentity | None = None
    if program:
        kind, version = _kind_and_version_from_program(program)
        version = version or str(env.get("TERM_PROGRAM_VERSION") or "").strip() or None
        program_identity = TerminalIdentity(
            kind,
            program,
            multiplexer=multiplexer,
            term_program=program,
            version=version,
            term=str(env.get("TERM") or "").strip() or None,
            multiplexer_version=mux_version,
            source=TerminalIdentitySource.TERM_PROGRAM,
            source_variable="TERM_PROGRAM",
        )
        if program_identity.is_ide_terminal:
            return program_identity

    # IDE 可继承外层终端的 TERM_PROGRAM，须先处理 JetBrains 明确标识。
    terminal_emulator = str(env.get("TERMINAL_EMULATOR") or "").strip()
    if terminal_emulator.casefold() == "jetbrains-jediterm":
        return TerminalIdentity(
            TerminalKind.JETBRAINS_JEDITERM,
            "JetBrains-JediTerm",
            multiplexer=multiplexer,
            term=str(env.get("TERM") or "").strip() or None,
            multiplexer_version=mux_version,
            source=TerminalIdentitySource.DIRECT_SIGNAL,
            source_variable="TERMINAL_EMULATOR",
        )

    if program_identity is not None:
        return program_identity

    direct_signals = (
        ("WEZTERM_VERSION", TerminalKind.WEZTERM, "WezTerm"),
        ("ITERM_SESSION_ID", TerminalKind.ITERM2, "iTerm2"),
        ("ITERM_PROFILE", TerminalKind.ITERM2, "iTerm2"),
        ("ITERM_PROFILE_NAME", TerminalKind.ITERM2, "iTerm2"),
        ("TERM_SESSION_ID", TerminalKind.APPLE_TERMINAL, "Apple Terminal"),
        ("KITTY_WINDOW_ID", TerminalKind.KITTY, "Kitty"),
        ("ALACRITTY_SOCKET", TerminalKind.ALACRITTY, "Alacritty"),
        ("KONSOLE_VERSION", TerminalKind.KONSOLE, "Konsole"),
        ("GNOME_TERMINAL_SCREEN", TerminalKind.GNOME_TERMINAL, "GNOME Terminal"),
        ("VTE_VERSION", TerminalKind.VTE, "VTE"),
        ("WT_SESSION", TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
    )
    for variable, kind, name in direct_signals:
        value = str(env.get(variable) or "").strip()
        if value:
            return TerminalIdentity(
                kind,
                name,
                multiplexer=multiplexer,
                version=value if variable.endswith("VERSION") else None,
                term=str(env.get("TERM") or "").strip() or None,
                multiplexer_version=mux_version,
                source=TerminalIdentitySource.DIRECT_SIGNAL,
                source_variable=variable,
            )

    if multiplexer is TerminalKind.TMUX:
        return TerminalIdentity(
            TerminalKind.TMUX,
            "tmux",
            multiplexer=multiplexer,
            multiplexer_version=mux_version,
            source=TerminalIdentitySource.MULTIPLEXER,
            source_variable="TMUX",
        )

    term = str(env.get("TERM") or "").strip()
    kind = _kind_from_term(term)
    return TerminalIdentity(
        kind,
        term or "unknown",
        multiplexer=multiplexer,
        term=term or None,
        multiplexer_version=mux_version,
        source=(
            TerminalIdentitySource.TERM
            if term
            else TerminalIdentitySource.UNKNOWN
        ),
        source_variable="TERM" if term else None,
    )


def _detect_multiplexer(
    env: typing.Mapping[str, str],
) -> tuple[TerminalKind | None, str | None]:
    """按环境信号识别外层复用器。"""

    if (
        str(env.get("ZELLIJ") or "").strip()
        or str(env.get("ZELLIJ_SESSION_NAME") or "").strip()
        or str(env.get("ZELLIJ_VERSION") or "").strip()
    ):
        return TerminalKind.ZELLIJ, str(env.get("ZELLIJ_VERSION") or "").strip() or None
    if str(env.get("TMUX") or "").strip() or str(env.get("TMUX_PANE") or "").strip():
        return TerminalKind.TMUX, _tmux_version(env)
    return None, None


def _tmux_version(env: typing.Mapping[str, str]) -> str | None:
    """读取可选的 tmux 版本环境值。"""

    value = str(env.get("TMUX_VERSION") or "").strip()
    return value or None


def _kind_and_version_from_program(value: str) -> tuple[TerminalKind, str | None]:
    """将 TERM_PROGRAM 或 tmux termtype 映射为终端身份。"""

    raw = str(value or "").strip()
    program_token, _, suffix = raw.partition(" ")
    normalized = re.sub(r"[ ._\-]", "", raw).casefold()
    normalized_token = re.sub(r"[ ._\-]", "", program_token).casefold()
    known = {
        "appleterminal": TerminalKind.APPLE_TERMINAL,
        "ghostty": TerminalKind.GHOSTTY,
        "itermapp": TerminalKind.ITERM2,
        "iterm2": TerminalKind.ITERM2,
        "kitty": TerminalKind.KITTY,
        "alacritty": TerminalKind.ALACRITTY,
        "konsole": TerminalKind.KONSOLE,
        "foot": TerminalKind.FOOT,
        "rio": TerminalKind.RIO,
        "warp": TerminalKind.WARP,
        "warpterminal": TerminalKind.WARP,
        "gnometerminal": TerminalKind.GNOME_TERMINAL,
        "vscode": TerminalKind.VSCODE,
        "vscodeinsiders": TerminalKind.VSCODE,
        "zed": TerminalKind.ZED,
        "wezterm": TerminalKind.WEZTERM,
        "windowsterminal": TerminalKind.WINDOWS_TERMINAL,
        "jetbrainsjediterm": TerminalKind.JETBRAINS_JEDITERM,
    }
    match = known.get(normalized, known.get(normalized_token, TerminalKind.UNKNOWN))
    if match is TerminalKind.UNKNOWN:
        match = _kind_from_term(raw)

    version = suffix.strip(" /-") or None
    if version is None and raw.casefold().startswith("wezterm"):
        version = raw[len("wezterm"):].strip(" /-") or None
    return match, version


def _kind_from_term(value: str) -> TerminalKind:
    """将 TERM 值按保守规则映射为终端身份。"""

    text = str(value or "").strip().casefold()
    if text == "dumb":
        return TerminalKind.DUMB
    if "kitty" in text:
        return TerminalKind.KITTY
    if "alacritty" in text:
        return TerminalKind.ALACRITTY
    if "ghostty" in text:
        return TerminalKind.GHOSTTY
    if "wezterm" in text:
        return TerminalKind.WEZTERM
    if "konsole" in text:
        return TerminalKind.KONSOLE
    if text == "foot" or text.endswith("-foot"):
        return TerminalKind.FOOT
    if text == "rio" or text.startswith("rio-"):
        return TerminalKind.RIO
    if text == "warp" or text.startswith("warp-"):
        return TerminalKind.WARP
    if "vte" in text:
        return TerminalKind.VTE
    if "tmux" in text:
        return TerminalKind.TMUX
    if "zellij" in text:
        return TerminalKind.ZELLIJ
    return TerminalKind.UNKNOWN


def _query_tmux_client() -> tuple[str, str] | None:
    """读取 tmux 客户端的 termtype 和 TERM。"""

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "#{client_termtype}\t#{client_termname}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TMUX_QUERY_TIMEOUT_SEC,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    term_type, _, name = result.stdout.strip().partition("\t")
    return (term_type.strip(), name.strip()) if name or term_type else None


if __name__ == '__main__':
    pass
