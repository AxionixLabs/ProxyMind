# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_app.native_coding.encoding import (
    DecodedProcessOutput,
    UTF8_ENCODING,
    UTF8_SIG_ENCODING,
    decode_process_output_details,
    normalize_process_output_encoding
)
from .process_capture import CapturedProcessResult

OUTPUT_STREAMS = ("stdout", "stderr")
UTF8_BOM       = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class DecodedCapturedOutput(object):
    """保存一次进程捕获的统一解码视图。"""

    stdout: str
    stderr: str
    output_lines: tuple[str, ...]
    encodings: tuple[str, ...]
    stream_encodings: dict[str, tuple[str, ...]]
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class StreamEncodingHint(object):
    """保存流级编码提示及其是否具有强制性。"""

    encoding: str = ""
    authoritative: bool = False


class CapturedOutputDecoder(object):
    """根据显式编码、BOM 和片段证据解码进程捕获结果。"""

    def __init__(self, *, encoding: str = "auto", line_limit: int = 1000) -> None:
        self.encoding   = normalize_process_output_encoding(encoding)
        self.line_limit = max(20, int(line_limit or 20))

    def decode(self, capture: CapturedProcessResult) -> DecodedCapturedOutput:
        """解码 stdout、stderr 和有序输出行。"""
        records_by_stream = {
            stream: tuple(
                record.data
                for record in capture.output_records
                if record.stream == stream
            )
            for stream in OUTPUT_STREAMS
        }
        raw_by_stream = {
            "stdout": capture.stdout or b"",
            "stderr": capture.stderr or b"",
        }
        prefix_partial_by_stream = {
            "stdout": bool(capture.stdout_prefix_partial),
            "stderr": bool(capture.stderr_prefix_partial),
        }

        hints = {
            stream: self._stream_hint(
                records_by_stream[stream],
                raw=raw_by_stream[stream],
                prefix_partial=prefix_partial_by_stream[stream],
            )
            for stream in OUTPUT_STREAMS
        }

        decoded_streams: dict[str, DecodedProcessOutput] = {}
        for stream in OUTPUT_STREAMS:
            source = self._stream_source(
                raw_by_stream[stream],
                prefix_partial=prefix_partial_by_stream[stream],
            )
            decoded_streams[stream] = self._decode_stream(
                source,
                hint=hints[stream],
            )

        output_lines: list[str] = []

        line_encodings: dict[str, list[str]] = {
            stream: [] for stream in OUTPUT_STREAMS
        }

        line_ambiguous: bool = False

        for record in capture.output_records:
            decoded = self._decode_segment(
                record.data,
                hint=hints.get(record.stream, StreamEncodingHint()),
            )
            text = decoded.text.rstrip()
            if not text:
                continue
            if len(text) > self.line_limit or record.truncated:
                text = f"{text[:self.line_limit - 3]}..."
            output_lines.append(text)
            line_ambiguous = line_ambiguous or decoded.ambiguous
            self._extend_unique(
                line_encodings.setdefault(record.stream, []),
                decoded.encodings,
            )

        stream_encodings: dict[str, tuple[str, ...]] = {}
        encodings: list[str] = []
        ambiguous = line_ambiguous

        for stream in OUTPUT_STREAMS:
            decoded = decoded_streams[stream]

            values: list[str] = []
            self._extend_unique(values, decoded.encodings)
            self._extend_unique(values, line_encodings.get(stream, []))
            stream_encodings[stream] = tuple(values)
            self._extend_unique(encodings, values)
            ambiguous = ambiguous or decoded.ambiguous

        return DecodedCapturedOutput(
            stdout=decoded_streams["stdout"].text,
            stderr=decoded_streams["stderr"].text,
            output_lines=tuple(output_lines),
            encodings=tuple(encodings),
            stream_encodings=stream_encodings,
            ambiguous=ambiguous,
        )

    def _stream_hint(
        self,
        records: tuple[bytes, ...],
        *,
        raw: bytes,
        prefix_partial: bool,
    ) -> StreamEncodingHint:
        """根据显式编码、BOM 或片段证据返回流编码提示。"""
        if self.encoding != "auto":
            return StreamEncodingHint(
                encoding=self.encoding,
                authoritative=True,
            )

        if not prefix_partial and raw.startswith(UTF8_BOM):
            return StreamEncodingHint(
                encoding=UTF8_ENCODING,
                authoritative=True,
            )
        if records and records[0].startswith(UTF8_BOM):
            return StreamEncodingHint(
                encoding=UTF8_ENCODING,
                authoritative=True,
            )
        return StreamEncodingHint(
            encoding=self._segment_evidence_encoding(records),
        )

    def _decode_stream(
        self,
        data: bytes,
        *,
        hint: StreamEncodingHint,
    ) -> DecodedProcessOutput:
        """按行解码完整输出流并保留换行符。"""
        if not data:
            return DecodedProcessOutput(text="", encodings=(), ambiguous=False)
        if self.encoding != "auto":
            return decode_process_output_details(data, encoding=self.encoding)

        parts: list[str]     = []
        encodings: list[str] = []
        ambiguous: bool      = False

        for body, ending in self._byte_segments(data):
            decoded = self._decode_segment(body, hint=hint)
            parts.append(decoded.text)
            parts.append(ending.decode("ascii"))
            self._extend_unique(encodings, decoded.encodings)
            ambiguous = ambiguous or decoded.ambiguous

        return DecodedProcessOutput(
            text="".join(parts),
            encodings=tuple(encodings),
            ambiguous=ambiguous,
        )

    def _decode_segment(
        self,
        data: bytes,
        *,
        hint: StreamEncodingHint,
    ) -> DecodedProcessOutput:
        """使用流提示和本地编码偏好解码单个片段。"""
        if not data:
            return DecodedProcessOutput(text="", encodings=(), ambiguous=False)
        if self.encoding != "auto":
            return decode_process_output_details(data, encoding=self.encoding)

        normalized_hint = self._normalized_hint(hint.encoding)
        if normalized_hint and hint.authoritative:
            hinted_encoding = (
                UTF8_SIG_ENCODING
                if normalized_hint == UTF8_ENCODING and data.startswith(UTF8_BOM)
                else normalized_hint
            )
            return DecodedProcessOutput(
                text=data.decode(hinted_encoding, errors="replace"),
                encodings=(normalized_hint,),
                ambiguous=False,
            )

        automatic = decode_process_output_details(data)
        if not automatic.ambiguous:
            return automatic

        hinted_text = self._strict_decode(data, normalized_hint)
        if normalized_hint and hinted_text is not None:
            return DecodedProcessOutput(
                text=hinted_text,
                encodings=(normalized_hint,),
                ambiguous=True,
            )

        preferred      = self._preferred_encoding()
        preferred_text = self._strict_decode(data, preferred)
        utf8_text      = self._strict_decode(data, UTF8_ENCODING)

        if (
            preferred
            and preferred != UTF8_ENCODING
            and preferred_text is not None
            and utf8_text is not None
            and not self._has_strong_utf8_sequence(data)
            and self._contains_cjk(preferred_text)
            and not self._contains_cjk(utf8_text)
        ):
            return DecodedProcessOutput(
                text=preferred_text,
                encodings=(preferred,),
                ambiguous=True,
            )

        return automatic

    def _segment_evidence_encoding(self, records: tuple[bytes, ...]) -> str:
        """从无歧义非 UTF-8 片段中提取一致的流级编码证据。"""
        evidence: list[str] = []

        for data in records:
            if not data or not any(byte >= 0x80 for byte in data):
                continue

            decoded = decode_process_output_details(data)
            if decoded.ambiguous or len(decoded.encodings) != 1:
                continue

            encoding = self._normalized_hint(decoded.encodings[0])
            if not encoding or encoding == UTF8_ENCODING:
                continue
            if self._strict_decode(data, encoding) is None:
                continue
            if encoding not in evidence:
                evidence.append(encoding)

        return evidence[0] if len(evidence) == 1 else ""

    @staticmethod
    def _stream_source(
        raw: bytes,
        *,
        prefix_partial: bool,
    ) -> bytes:
        """在字节前缀截断时跳过首个不完整行。"""
        if not raw or not prefix_partial:
            return raw

        for index, byte in enumerate(raw):
            if byte == 10:
                aligned = raw[index + 1:]
                return aligned or raw
            if byte == 13:
                offset = 2 if index + 1 < len(raw) and raw[index + 1] == 10 else 1
                aligned = raw[index + offset:]
                return aligned or raw

        return raw

    @staticmethod
    def _byte_segments(data: bytes) -> list[tuple[bytes, bytes]]:
        """把字节流拆成正文和单字节兼容换行符。"""
        segments: list[tuple[bytes, bytes]] = []
        for segment in data.splitlines(keepends=True):
            for ending in (b"\r\n", b"\n", b"\r"):
                if segment.endswith(ending):
                    segments.append((segment[:-len(ending)], ending))
                    break
            else:
                segments.append((segment, b""))
        return segments

    @staticmethod
    def _strict_decode(data: bytes, encoding: str) -> str | None:
        """严格解码字节，失败时返回空提示。"""
        if not encoding:
            return None
        try:
            return data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            return None

    @staticmethod
    def _has_strong_utf8_sequence(data: bytes) -> bool:
        """判断字节中是否包含三字节或四字节 UTF-8 起始字节。"""
        return any(0xE0 <= byte <= 0xF4 for byte in data)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        """判断文本是否包含常见 CJK 字符。"""
        return any(
            0x3400 <= ord(char) <= 0x4DBF
            or 0x4E00 <= ord(char) <= 0x9FFF
            or 0xF900 <= ord(char) <= 0xFAFF
            for char in text
        )

    @staticmethod
    def _preferred_encoding() -> str:
        """返回当前系统首选进程输出编码。"""
        try:
            return normalize_process_output_encoding("system")
        except ValueError:
            return ""

    @staticmethod
    def _normalized_hint(value: str) -> str:
        """返回有效的规范编码提示。"""
        if not value:
            return ""
        try:
            return normalize_process_output_encoding(value)
        except ValueError:
            return ""

    @staticmethod
    def _extend_unique(target: list[str], values: typing.Iterable[str]) -> None:
        """按出现顺序追加尚未记录的编码。"""
        for value in values:
            try:
                normalized = normalize_process_output_encoding(value)
            except ValueError:
                normalized = value
            if normalized == UTF8_SIG_ENCODING:
                normalized = UTF8_ENCODING
            if normalized not in target:
                target.append(normalized)


if __name__ == '__main__':
    pass
