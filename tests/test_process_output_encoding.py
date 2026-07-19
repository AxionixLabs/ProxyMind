# -*- coding: utf-8 -*-

import asyncio

import mind_app.native_coding.encoding as output_encoding
from mind_app.native_coding.exec.output_decoder import CapturedOutputDecoder
from mind_app.native_coding.exec.process_capture import (
    CapturedOutputLine,
    CapturedProcessResult,
    OrderedOutputBuffer,
    _CaptureBuffer,
)


def run_async(value: object) -> object:
    """同步测试中运行异步调用。"""
    return asyncio.run(value)


def use_utf8_and_gbk_candidates(monkeypatch: object) -> None:
    """固定自动解码候选，避免依赖测试系统区域设置。"""
    monkeypatch.setattr(
        output_encoding,
        "process_output_encodings",
        lambda: ["utf-8", "gbk"]
    )


def captured_output(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    records: tuple[CapturedOutputLine, ...] = (),
    stdout_dropped: int = 0,
    stderr_dropped: int = 0,
    stdout_prefix_partial: bool = False,
    stderr_prefix_partial: bool = False,
) -> CapturedProcessResult:
    """构造稳定的进程捕获结果。"""
    return CapturedProcessResult(
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        output_records=records,
        stdout_dropped=stdout_dropped,
        stderr_dropped=stderr_dropped,
        timed_out=False,
        elapsed_ms=1,
        stdout_prefix_partial=stdout_prefix_partial,
        stderr_prefix_partial=stderr_prefix_partial,
    )


def test_auto_decoding_resolves_ambiguous_gbk_text(monkeypatch: object) -> None:
    """GBK 字节同时是合法 UTF-8 时选择可读中文。"""
    use_utf8_and_gbk_candidates(monkeypatch)

    decoded = output_encoding.decode_process_output_details(bytes.fromhex("d2bb"))

    assert decoded.text == "一"
    assert decoded.encodings == ("gbk",)
    assert decoded.ambiguous is True


def test_auto_decoding_supports_mixed_line_encodings(monkeypatch: object) -> None:
    """同一输出流中的 UTF-8 和 GBK 行分别解码。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    data = "一\r\n".encode("utf-8") + bytes.fromhex("d6d0cec4b2e2cad40d0a")

    decoded = output_encoding.decode_process_output_details(data)

    assert decoded.text == "一\r\n中文测试\r\n"
    assert decoded.encodings == ("utf-8", "gbk")
    assert decoded.ambiguous is False


def test_explicit_output_encoding_is_authoritative() -> None:
    """显式编码不执行自动文本质量选择。"""
    decoded = output_encoding.decode_process_output_details(
        bytes.fromhex("d2bb"),
        encoding="utf-8"
    )

    assert decoded.text == "һ"
    assert decoded.encodings == ("utf-8",)
    assert decoded.ambiguous is False


def test_output_encoding_rejects_non_byte_line_compatible_codec() -> None:
    """非单字节换行编码在进程启动前被拒绝。"""
    try:
        output_encoding.normalize_process_output_encoding("utf-16")
    except ValueError as exc:
        assert str(exc) == "unsupported process output encoding: utf-16"
    else:
        raise AssertionError("utf-16 should be rejected")


def test_auto_decoding_prefers_structurally_strong_utf8(monkeypatch: object) -> None:
    """完整三字节和四字节 UTF-8 序列不被系统编码误判。"""
    use_utf8_and_gbk_candidates(monkeypatch)

    for expected in ("✓", "😀"):
        decoded = output_encoding.decode_process_output_details(expected.encode("utf-8"))
        assert decoded.text == expected
        assert decoded.encodings == ("utf-8",)


def test_auto_output_encodings_canonicalize_utf8_aliases(monkeypatch: object) -> None:
    """UTF-8 别名在自动解码候选中合并为规范名称。"""
    monkeypatch.setattr(
        output_encoding,
        "process_output_encodings",
        lambda: ["utf-8-sig", "UTF-8", "utf_8", "gbk"]
    )

    assert output_encoding._auto_output_encodings() == ["utf-8", "gbk"]


def test_ordered_output_buffer_preserves_split_multibyte_character() -> None:
    """跨 chunk 的 UTF-8 字符在完整行形成前保持原始字节。"""
    async def probe() -> tuple[str, ...]:
        buffer = OrderedOutputBuffer()
        raw = "中文测试\n".encode("utf-8")
        for chunk in (raw[:1], raw[1:2], raw[2:5], raw[5:]):
            await buffer.append("stdout", chunk)
        return await buffer.snapshot()

    assert run_async(probe()) == ("中文测试",)


def test_ordered_output_buffer_preserves_split_crlf() -> None:
    """跨 chunk 的 CRLF 只形成一个行结束符。"""
    async def probe() -> tuple[tuple[str, bytes], ...]:
        buffer = OrderedOutputBuffer()
        await buffer.append("stderr", b"first\r")
        await buffer.append("stderr", b"\nsecond\rthird\n")
        records = await buffer.snapshot_records()
        return tuple((item.stream, item.data) for item in records)

    assert run_async(probe()) == (
        ("stderr", b"first"),
        ("stderr", b"second"),
        ("stderr", b"third"),
    )


def test_capture_buffer_tracks_line_boundary_after_truncation() -> None:
    """字节缓冲区区分完整行边界和半行截断。"""
    aligned = _CaptureBuffer(limit_bytes=10)
    aligned.append(b"old\nkept\nnext\n")

    partial = _CaptureBuffer(limit_bytes=10)
    partial.append(b"xxkept\nnext\n")

    split_crlf = _CaptureBuffer(limit_bytes=5)
    split_crlf.append(b"old\r\nkept")

    assert aligned.bytes() == b"kept\nnext\n"
    assert aligned.prefix_partial is False
    assert partial.bytes() == b"kept\nnext\n"
    assert partial.prefix_partial is True
    assert split_crlf.bytes() == b"kept"
    assert split_crlf.prefix_partial is False


def test_ordered_output_buffer_bounds_unfinished_line() -> None:
    """无换行输出不会让有序行缓冲无限增长。"""

    async def scenario() -> tuple[tuple[CapturedOutputLine, ...], tuple[str, ...]]:
        buffer = OrderedOutputBuffer(
            max_line_chars=20,
            max_line_bytes=24,
        )
        await buffer.append("stdout", b"a" * 16)
        await buffer.append("stdout", b"a" * 32)
        return await buffer.snapshot_records(), await buffer.snapshot()

    records, lines = run_async(scenario())

    assert records == (
        CapturedOutputLine("stdout", b"a" * 24, truncated=True),
    )
    assert lines == ("a" * 17 + "...",)


def test_captured_output_decoder_prefers_system_cjk_for_weak_utf8(
    monkeypatch: object,
) -> None:
    """缺少流级证据时优先选择系统编码中的可读中文。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    monkeypatch.setattr(
        CapturedOutputDecoder,
        "_preferred_encoding",
        staticmethod(lambda: "gbk"),
    )
    raw = "目录\r\n".encode("gbk")

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(CapturedOutputLine("stdout", raw[:-2]),),
    ))

    assert decoded.stdout == "目录\r\n"
    assert decoded.output_lines == ("目录",)
    assert decoded.stream_encodings["stdout"] == ("gbk",)
    assert decoded.ambiguous is True


def test_captured_output_decoder_uses_utf8_bom_stream_evidence(
    monkeypatch: object,
) -> None:
    """UTF-8 BOM 为同一流中的两字节歧义提供先验。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    monkeypatch.setattr(
        CapturedOutputDecoder,
        "_preferred_encoding",
        staticmethod(lambda: "gbk"),
    )
    first = b"\xef\xbb\xbf" + "中文".encode("utf-8")
    second = "é".encode("utf-8")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert decoded.stdout == "中文\né\n"
    assert decoded.output_lines == ("中文", "é")
    assert decoded.stream_encodings["stdout"] == ("utf-8",)


def test_captured_output_decoder_treats_bom_as_authoritative(
    monkeypatch: object,
) -> None:
    """BOM 声明不会因后续片段更像系统编码而切换。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    first = b"\xef\xbb\xbf" + "中文".encode("utf-8")
    second = "中文".encode("gbk")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert decoded.stdout == "中文\n" + second.decode("utf-8", errors="replace") + "\n"
    assert decoded.stream_encodings["stdout"] == ("utf-8",)
    assert decoded.ambiguous is False


def test_captured_output_decoder_decodes_ambiguous_mixed_stream(
    monkeypatch: object,
) -> None:
    """同一流中的 UTF-8 和弱歧义 GBK 分别按片段解码。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    monkeypatch.setattr(
        CapturedOutputDecoder,
        "_preferred_encoding",
        staticmethod(lambda: "gbk"),
    )
    first = "中文".encode("utf-8")
    second = "目录".encode("gbk")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert decoded.stdout == "中文\n目录\n"
    assert decoded.output_lines == ("中文", "目录")
    assert decoded.stream_encodings["stdout"] == ("utf-8", "gbk")
    assert decoded.ambiguous is True


def test_captured_output_decoder_uses_gbk_segment_evidence(
    monkeypatch: object,
) -> None:
    """明确和弱歧义 GBK 片段分别按自身证据解码。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    first = "中文测试".encode("gbk")
    second = "目录".encode("gbk")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert decoded.stdout == "中文测试\n目录\n"
    assert decoded.output_lines == ("中文测试", "目录")
    assert decoded.stream_encodings["stdout"] == ("gbk",)


def test_captured_output_decoder_keeps_gbk_stream_hint_soft(
    monkeypatch: object,
) -> None:
    """GBK 片段先验不会覆盖后续无歧义 UTF-8 片段。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    first = "中文测试".encode("gbk")
    second = "中文".encode("utf-8")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert decoded.stdout == "中文测试\n中文\n"
    assert decoded.output_lines == ("中文测试", "中文")
    assert decoded.stream_encodings["stdout"] == ("gbk", "utf-8")


def test_captured_output_decoder_rejects_false_utf8_stream_evidence(
    monkeypatch: object,
) -> None:
    """合法 GBK 偶合三字节 UTF-8 结构时不建立错误先验。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    monkeypatch.setattr(
        CapturedOutputDecoder,
        "_preferred_encoding",
        staticmethod(lambda: "gbk"),
    )
    first = "陳砫".encode("gbk")
    second = "目录".encode("gbk")
    raw = first + b"\n" + second + b"\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", first),
            CapturedOutputLine("stdout", second),
        ),
    ))

    assert first.decode("utf-8") != "陳砫"
    ambiguous_first = first.decode("utf-8")
    assert decoded.stdout == f"{ambiguous_first}\n目录\n"
    assert decoded.output_lines == (ambiguous_first, "目录")
    assert decoded.stream_encodings["stdout"] == ("utf-8", "gbk")
    assert decoded.ambiguous is True


def test_captured_output_decoder_keeps_stream_encodings_separate(
    monkeypatch: object,
) -> None:
    """stdout 和 stderr 分别建立编码先验。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    monkeypatch.setattr(
        CapturedOutputDecoder,
        "_preferred_encoding",
        staticmethod(lambda: "gbk"),
    )
    stdout = "中文\n".encode("utf-8")
    stderr = "目录\n".encode("gbk")

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=stdout,
        stderr=stderr,
        records=(
            CapturedOutputLine("stdout", stdout[:-1]),
            CapturedOutputLine("stderr", stderr[:-1]),
        ),
    ))

    assert decoded.stdout == "中文\n"
    assert decoded.stderr == "目录\n"
    assert decoded.output_lines == ("中文", "目录")
    assert decoded.stream_encodings == {
        "stdout": ("utf-8",),
        "stderr": ("gbk",),
    }


def test_captured_output_decoder_preserves_complete_line_boundary(
    monkeypatch: object,
) -> None:
    """字节截断发生在换行后时保留第一条完整行。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    raw = b"kept\nnext\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(
            CapturedOutputLine("stdout", b"kept"),
            CapturedOutputLine("stdout", b"next"),
        ),
        stdout_dropped=32,
    ))

    assert decoded.stdout == "kept\nnext\n"
    assert decoded.output_lines == ("kept", "next")


def test_captured_output_decoder_discards_only_partial_first_line(
    monkeypatch: object,
) -> None:
    """半行截断只丢弃第一个不完整片段。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    raw = b"partial\nkept\n"

    decoded = CapturedOutputDecoder().decode(captured_output(
        stdout=raw,
        records=(CapturedOutputLine("stdout", b"kept"),),
        stdout_dropped=32,
        stdout_prefix_partial=True,
    ))

    assert decoded.stdout == "kept\n"
    assert decoded.output_lines == ("kept",)


def test_captured_output_decoder_does_not_restore_unbounded_line(
    monkeypatch: object,
) -> None:
    """无换行尾缓冲不会从有序记录恢复完整历史行。"""
    use_utf8_and_gbk_candidates(monkeypatch)
    complete = b"HEAD-" + b"x" * 40 + b"-TAIL"
    tail = complete[-16:]

    decoded = CapturedOutputDecoder(line_limit=100).decode(captured_output(
        stdout=tail,
        records=(CapturedOutputLine("stdout", complete, truncated=True),),
        stdout_dropped=len(complete) - len(tail),
        stdout_prefix_partial=True,
    ))

    assert decoded.stdout == tail.decode("ascii")
    assert decoded.output_lines == ("HEAD-" + "x" * 40 + "-TAIL...",)
