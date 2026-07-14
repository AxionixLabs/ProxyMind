# -*- coding: utf-8 -*-

import asyncio

import mind_app.native_coding.encoding as output_encoding
from mind_app.native_coding.exec.process_capture import OrderedOutputBuffer


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
