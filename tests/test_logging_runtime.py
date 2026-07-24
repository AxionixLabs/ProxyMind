# -*- coding: utf-8 -*-

from pathlib import Path

import pytest
from loguru import logger

from mind_app.reporting import RunReport
from mind_app.stream_io.output_record import StreamRecordWriter


@pytest.mark.anyio
async def test_run_report_separates_plain_debug_and_output_records(
    tmp_path: Path,
) -> None:
    report = RunReport(str(tmp_path), label="logging-test")
    writer = StreamRecordWriter(report.log_papers)

    await writer.open()
    writer.write("assistant-output-marker\n")
    await writer.close()

    logger.debug("diagnostic-log-marker")
    report.close()

    debug_path = Path(report.debug_log)
    output_path = Path(report.log_papers)
    debug_text = debug_path.read_text(encoding="utf-8")
    output_text = output_path.read_text(encoding="utf-8")

    assert debug_path != output_path
    assert "diagnostic-log-marker" in debug_text
    assert "assistant-output-marker" not in debug_text
    assert "assistant-output-marker" in output_text
    assert "diagnostic-log-marker" not in output_text
    assert "\x1b[" not in debug_text
    assert "<level>" not in debug_text
    assert "<green>" not in debug_text

    closed_text = debug_path.read_text(encoding="utf-8")
    logger.error("closed-log-marker")
    assert debug_path.read_text(encoding="utf-8") == closed_text
