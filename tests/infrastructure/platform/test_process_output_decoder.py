# -*- coding: utf-8 -*-

import os
from types import SimpleNamespace

import pytest

from infrastructure.platform.encoding import decode_process_output_details
from infrastructure.platform.output_decoder import StreamingProcessOutputDecoder
from infrastructure.platform.process_sessions import ProcessSession
from infrastructure.platform.process_sessions import ProcessSessionManager
from infrastructure.platform.process_sessions import ProcessSessionSpec


@pytest.mark.parametrize(
    "value",
    (
        "中文输出：你好",
        "前缀🙂后缀",
    ),
)
def test_streaming_decoder_preserves_utf8_across_every_byte_boundary(
    value: str,
) -> None:
    """确保 UTF-8 字符在任意读取边界都只产生一次完整文本。"""
    payload = value.encode("utf-8")
    decoder = StreamingProcessOutputDecoder()

    chunks = [decoder.feed(bytes((byte,))) for byte in payload]
    chunks.append(decoder.finish())

    assert "".join(chunks) == value
    assert all("\ufffd" not in chunk for chunk in chunks)


def test_streaming_decoder_preserves_explicit_gbk_across_every_byte_boundary() -> None:
    """确保显式传统编码同样由增量 codec 保存字符边界。"""
    value = "中文输出：你好"
    decoder = StreamingProcessOutputDecoder(encoding="gbk")

    chunks = [decoder.feed(bytes((byte,))) for byte in value.encode("gbk")]
    chunks.append(decoder.finish())

    assert "".join(chunks) == value
    assert all("\ufffd" not in chunk for chunk in chunks)


@pytest.mark.skipif(os.name != "nt", reason="Windows output candidates are required")
def test_streaming_decoder_auto_detects_split_windows_gbk() -> None:
    """确保 Windows 自动模式不会在 GBK 读取边界制造乱码。"""
    value = "中文输出：你好"
    decoder = StreamingProcessOutputDecoder()

    chunks = [decoder.feed(bytes((byte,))) for byte in value.encode("gbk")]
    chunks.append(decoder.finish())

    assert "".join(chunks) == value
    assert all("\ufffd" not in chunk for chunk in chunks)


def test_streaming_decoder_releases_incomplete_explicit_sequence_at_eof() -> None:
    """确保 EOF 才把确实残缺的显式编码序列投影为替换字符。"""
    decoder = StreamingProcessOutputDecoder(encoding="utf-8")

    assert decoder.feed("中".encode("utf-8")[:2]) == ""
    assert decoder.finish() == "\ufffd"
    assert decoder.finish() == ""


def test_streaming_decoder_reuses_ambiguous_encoding_score() -> None:
    """确保流式模式与完整输出对合法多编码短片段作出相同选择。"""
    payload = b"\xc2\xa1"
    expected = decode_process_output_details(payload)
    decoder = StreamingProcessOutputDecoder()

    assert decoder.feed(payload) == expected.text
    assert decoder.selected_encoding == expected.encodings[0]


@pytest.mark.anyio
async def test_process_session_decodes_split_streams_before_publishing() -> None:
    """确保持续会话不会把系统读取边界泄漏给增量事件和快照。"""
    manager = ProcessSessionManager()
    session = ProcessSession(
        session_id="exec_split_output",
        spec=ProcessSessionSpec(
            command="split output",
            args=("split-output",),
            cwd=".",
            display_cwd=".",
            runtime={},
            origin="test",
            timeout_sec=30,
            idle_timeout_sec=30,
        ),
        process=SimpleNamespace(pid=1, returncode=None),
    )
    manager.sessions[session.session_id] = session
    stdout = "中文输出：你好".encode("utf-8")
    stderr = "错误🙂详情".encode("utf-8")

    await manager._record_output(session, "stdout", stdout[:2])
    await manager._record_output(session, "stderr", stderr[:1])
    initial = await manager.output_delta(session.session_id, revision=0)

    assert initial["revision"] == 0
    assert initial["items"] == []
    assert initial["snapshot"]["output_lines"] == []

    await manager._record_output(session, "stderr", stderr[1:])
    await manager._record_output(session, "stdout", stdout[2:])
    delta = await manager.output_delta(session.session_id, revision=0)

    assert [item["text"] for item in delta["items"]] == [
        "错误🙂详情",
        "中文输出：你好",
    ]
    assert delta["snapshot"]["output_lines"] == [
        "中文输出：你好",
        "错误🙂详情",
    ]
