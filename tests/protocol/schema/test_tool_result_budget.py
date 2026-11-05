import base64
import json

import pytest

from protocol.schema.tool_output import (
    TOOL_RESULT_IMAGE_MAX_BYTES,
    TOOL_RESULT_MAX_BYTES,
    TOOL_RESULT_METADATA_MAX_BYTES,
    ToolResultBudgetError,
    validate_tool_result_budget,
)


def envelope():
    return {"ok": True, "tool": "computer", "source": "client", "args": {},
            "text": "", "data": {}, "attachments": []}


def image_attachment(size):
    return {"kind": "image", "mime_type": "image/png",
            "data_url": "data:image/png;base64," + base64.b64encode(b"x" * size).decode()}


def size_of(result):
    return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())


def test_image_budget_accepts_large_image_without_changing_payload():
    result = envelope()
    result["attachments"] = [image_attachment(7 * 1024 * 1024)]
    before = result["attachments"][0]["data_url"]
    validate_tool_result_budget(result)
    assert result["attachments"][0]["data_url"] == before
    assert size_of(result) > TOOL_RESULT_METADATA_MAX_BYTES


def test_metadata_budget_counts_utf8_and_exact_boundary():
    result = envelope()
    remaining = TOOL_RESULT_METADATA_MAX_BYTES - size_of(result)
    result["text"] = "文" * (remaining // 3) + "x" * (remaining % 3)
    validate_tool_result_budget(result)
    result["text"] += "é"
    with pytest.raises(ToolResultBudgetError) as failure:
        validate_tool_result_budget(result)
    assert failure.value.code == "tool_result_metadata_too_large"
    assert failure.value.actual_bytes == TOOL_RESULT_METADATA_MAX_BYTES + 2


def test_full_envelope_budget_counts_all_images_and_exact_boundary():
    result = envelope()
    result["attachments"] = [image_attachment(6 * 1024 * 1024), image_attachment(5 * 1024 * 1024)]
    result["text"] = "x" * (TOOL_RESULT_MAX_BYTES - size_of(result))
    validate_tool_result_budget(result)
    result["text"] += "x"
    with pytest.raises(ToolResultBudgetError) as failure:
        validate_tool_result_budget(result)
    assert failure.value.code == "tool_result_too_large"
    assert failure.value.actual_bytes == TOOL_RESULT_MAX_BYTES + 1


def test_single_image_budget_is_independent_of_total():
    result = envelope()
    result["attachments"] = [image_attachment(TOOL_RESULT_IMAGE_MAX_BYTES + 1)]
    with pytest.raises(ToolResultBudgetError) as failure:
        validate_tool_result_budget(result)
    assert failure.value.code == "tool_result_image_too_large"


@pytest.mark.parametrize("value", ["data:image/png;base64,%%%", "data:image/jpeg;base64,eA==", "data:image/png;base64,"])
def test_invalid_inline_images_cannot_bypass_metadata_budget(value):
    result = envelope()
    result["attachments"] = [{"kind": "image", "mime_type": "image/png", "data_url": value}]
    with pytest.raises(ToolResultBudgetError, match="tool_result_image_invalid"):
        validate_tool_result_budget(result)


def test_non_image_and_attachment_metadata_remain_bounded():
    for attachment in [
        {"kind": "file", "data_url": "x" * TOOL_RESULT_METADATA_MAX_BYTES},
        {**image_attachment(8), "filename": "x" * TOOL_RESULT_METADATA_MAX_BYTES},
    ]:
        result = envelope()
        result["attachments"] = [attachment]
        with pytest.raises(ToolResultBudgetError, match="tool_result_metadata_too_large"):
            validate_tool_result_budget(result)
