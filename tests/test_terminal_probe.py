# -*- coding: utf-8 -*-

from frontends.terminal import probe_unix
from frontends.terminal.probe import (
    MAX_OSC_RESPONSE_BYTES,
    TerminalProbeMethod,
    TerminalDefaultColors,
    TerminalDefaultColorsCache,
    filter_terminal_color_responses,
    parse_terminal_color_responses,
)


def test_incomplete_color_response_is_preserved_for_input_replay() -> None:
    data = b"before\x1b]10;rgb:ffff/0000"

    assert filter_terminal_color_responses(data) == data


def test_non_color_osc_and_bracketed_paste_are_preserved() -> None:
    data = (
        b"\x1b[200~paste"
        b"\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\"
        b"\x1b[201~"
    )

    assert filter_terminal_color_responses(data) == data


def test_complete_invalid_color_response_is_filtered() -> None:
    data = b"a\x1b]10;not-a-color\x07b"

    assert filter_terminal_color_responses(data) == b"ab"


def test_oversized_incomplete_color_candidate_is_not_swallowed() -> None:
    data = b"\x1b]10;" + (b"x" * (MAX_OSC_RESPONSE_BYTES + 1))

    assert filter_terminal_color_responses(data) == data


def test_partial_default_color_pair_is_not_complete() -> None:
    colors = parse_terminal_color_responses(b"\x1b]10;#010203\x07")

    assert colors.foreground == (1, 2, 3)
    assert colors.background is None
    assert not colors.complete


def test_cache_discards_partial_default_color_pair() -> None:
    cache = TerminalDefaultColorsCache()

    colors = cache.get_or_probe(
        object(),
        object(),
        0.1,
        lambda _input, _output, _timeout: TerminalDefaultColors(
            background=(1, 2, 3),
        ),
    )

    assert colors.foreground is None
    assert colors.background is None
    assert colors.attempted
    assert colors.method is TerminalProbeMethod.CUSTOM


def test_cache_records_probe_failure_only_once() -> None:
    """验证失败探测也形成会话级一次性缓存。"""
    cache = TerminalDefaultColorsCache()
    calls: list[int] = []

    def probe(_input, _output, _timeout) -> TerminalDefaultColors:
        calls.append(1)
        raise OSError("terminal unavailable")

    first = cache.get_or_probe(None, None, 0.1, probe)
    second = cache.get_or_probe(None, None, 0.1, probe)

    assert first == second
    assert first.attempted
    assert not first.complete
    assert calls == [1]


def test_unix_reader_uses_fixed_total_buffer_limit(monkeypatch) -> None:
    replayed: list[bytes] = []

    monkeypatch.setattr(
        probe_unix.select,
        "select",
        lambda _read, _write, _error, _timeout: ([1], [], []),
    )
    monkeypatch.setattr(
        probe_unix.os,
        "read",
        lambda _descriptor, size: b"x" * size,
    )

    colors = probe_unix._read_unix_color_response(
        1,
        1.0,
        replayed.append,
    )

    assert not colors.complete
    assert colors.attempted
    assert colors.method is TerminalProbeMethod.UNIX_OSC
    assert replayed == [b"x" * probe_unix.MAX_STARTUP_PROBE_BYTES]


def test_unix_query_writer_completes_short_writes(monkeypatch) -> None:
    writes: list[bytes] = []

    def write(_descriptor: int, data: bytes) -> int:
        writes.append(data)
        return min(3, len(data))

    monkeypatch.setattr(probe_unix.os, "write", write)

    probe_unix._write_terminal_query(1)

    assert len(writes) > 1
    assert b"".join(chunk[:3] for chunk in writes) == probe_unix._terminal_color_query()


if __name__ == '__main__':
    pass
