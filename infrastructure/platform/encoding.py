# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import codecs
import locale
import os
import sys
import typing
import unicodedata
from dataclasses import dataclass

from metadata import const

UTF8_ENCODING = codecs.lookup("utf-8").name
UTF8_SIG_ENCODING = codecs.lookup("utf-8-sig").name


@dataclass(frozen=True, slots=True)
class DecodedProcessOutput(object):
    """记录进程输出的解码结果。"""

    text: str
    encodings: tuple[str, ...]
    ambiguous: bool


def decode_process_output(
    data: bytes,
    *,
    encoding: str = "auto"
) -> str:
    """按指定策略解码进程输出。"""
    return decode_process_output_details(data, encoding=encoding).text


def decode_process_output_details(
    data: bytes,
    *,
    encoding: str = "auto"
) -> DecodedProcessOutput:
    """解码进程输出并返回使用的编码信息。"""
    if not data:
        return DecodedProcessOutput(text="", encodings=(), ambiguous=False)

    selected = normalize_process_output_encoding(encoding)
    if selected != "auto":
        return DecodedProcessOutput(
            text=data.decode(selected, errors="replace"),
            encodings=(selected,),
            ambiguous=False
        )

    bom_encoding = _bom_encoding(data)
    if bom_encoding:
        return DecodedProcessOutput(
            text=data.decode(bom_encoding, errors="replace"),
            encodings=(bom_encoding,),
            ambiguous=False
        )

    text_parts: list[str] = []
    encodings: list[str] = []

    ambiguous: bool = False

    for segment in data.splitlines(keepends=True):
        body, ending = _split_line_ending(segment)
        decoded = _decode_auto_segment(body)
        text_parts.append(decoded.text)
        text_parts.append(ending.decode("ascii"))
        ambiguous = ambiguous or decoded.ambiguous
        for item in decoded.encodings:
            if item not in encodings:
                encodings.append(item)

    return DecodedProcessOutput(
        text="".join(text_parts),
        encodings=tuple(encodings),
        ambiguous=ambiguous
    )


def normalize_process_output_encoding(value: typing.Any) -> str:
    """把输出编码参数转换为规范名称。"""
    raw = str(value or "auto").strip() or "auto"

    lowered = raw.lower().replace("_", "-")
    if lowered == "auto":
        return "auto"
    if lowered in {"system", "locale"}:
        raw = locale.getpreferredencoding(False) or const.CHARSET

    try:
        encoding = codecs.lookup(raw).name
    except LookupError as exc:
        raise ValueError(f"unknown output encoding: {value}") from exc

    if not _supports_byte_line_boundaries(encoding):
        raise ValueError(f"unsupported process output encoding: {value}")
    return encoding


def _supports_byte_line_boundaries(encoding: str) -> bool:
    """判断编码是否可按单字节回车和换行安全切分。"""
    try:
        encoded = "text\r\n".encode(encoding)
        decoded = b"\r\n".decode(encoding, errors="strict")
    except (LookupError, UnicodeError):
        return False
    return encoded.endswith(b"\r\n") and decoded == "\r\n"


def _decode_auto_segment(data: bytes) -> DecodedProcessOutput:
    """从候选编码中选择文本质量较高的解码结果。"""
    if not data:
        return DecodedProcessOutput(text="", encodings=(), ambiguous=False)

    candidates: list[tuple[str, str]] = []
    for encoding in _auto_output_encodings():
        try:
            text = data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        if any(current_text == text for _, current_text in candidates):
            continue
        candidates.append((encoding, text))

    if not candidates:
        fallback = normalize_process_output_encoding(const.CHARSET)
        return DecodedProcessOutput(
            text=data.decode(fallback, errors="replace"),
            encodings=(fallback,),
            ambiguous=False
        )

    if len(candidates) == 1:
        encoding, text = candidates[0]
        return DecodedProcessOutput(text=text, encodings=(encoding,), ambiguous=False)

    selected_encoding, selected_text = max(
        candidates,
        key=lambda item: _decoded_text_score(
            item[1], encoding=item[0], data=data
        )
    )
    return DecodedProcessOutput(
        text=selected_text,
        encodings=(selected_encoding,),
        ambiguous=True
    )


def _auto_output_encodings() -> list[str]:
    """返回自动解码使用的规范候选编码。"""
    encodings: list[str] = []

    for item in process_output_encodings():
        try:
            encoding = normalize_process_output_encoding(item)
        except ValueError:
            continue
        if encoding == UTF8_SIG_ENCODING:
            encoding = UTF8_ENCODING
        if encoding not in encodings:
            encodings.append(encoding)

    return encodings


def _decoded_text_score(text: str, *, encoding: str, data: bytes) -> float:
    """计算候选文本的可读性分值。"""
    if not text:
        return 0.0

    normalized_encoding = normalize_process_output_encoding(encoding)

    score = sum(_character_score(char) for char in text) / len(text)

    if normalized_encoding == UTF8_ENCODING:
        score += _utf8_structure_score(data)

    preferred = normalize_process_output_encoding(
        locale.getpreferredencoding(False) or const.CHARSET
    )

    if normalized_encoding == preferred:
        score += 0.25

    return score


def _character_score(char: str) -> float:
    """返回单个字符的文本质量分值。"""
    codepoint = ord(char)
    category = unicodedata.category(char)

    if char in "\t ":
        return 0.2
    if char in "\r\n":
        return 0.0
    if category.startswith("C"):
        return -5.0
    if _is_cjk(codepoint):
        return 3.0
    if category.startswith(("L", "N")):
        if codepoint <= 0x024F:
            return 2.0
        return 1.0
    if category.startswith("P"):
        return 0.2
    if category.startswith("S"):
        return -1.0

    return 0.0


def _is_cjk(codepoint: int) -> bool:
    """判断码点是否位于常见中日韩文字区段。"""
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _utf8_structure_score(data: bytes) -> float:
    """根据 UTF-8 多字节结构返回置信加分。"""
    if not any(byte >= 0x80 for byte in data):
        return 0.5
    if any(byte >= 0xE0 for byte in data):
        return 5.0
    return 1.5


def _split_line_ending(value: bytes) -> tuple[bytes, bytes]:
    """分离字节行内容和行结束符。"""
    for ending in (b"\r\n", b"\n", b"\r"):
        if value.endswith(ending):
            return value[:-len(ending)], ending
    return value, b""


def _bom_encoding(data: bytes) -> str:
    """根据字节顺序标记返回编码名称。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return UTF8_SIG_ENCODING
    return ""


def process_output_encodings() -> list[str]:
    """返回进程输出的候选解码顺序。"""
    candidates: list[typing.Any] = [
        UTF8_SIG_ENCODING,
        const.CHARSET,
        sys.stdout.encoding,
        sys.stderr.encoding,
        locale.getpreferredencoding(False)
    ]

    if os.name == "nt":
        candidates.extend(["mbcs", "oem", "cp936", "gbk"])

    encodings: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        encoding = str(item or "").strip()
        if not encoding:
            continue
        key = encoding.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        encodings.append(encoding)

    return encodings


if __name__ == '__main__':
    pass
