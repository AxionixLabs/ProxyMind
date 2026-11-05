# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import base64
import binascii
import io
import json
import math
from collections.abc import Mapping

from PIL import Image
from PIL import UnidentifiedImageError

from agent.ports.desktop import (
    DesktopBounds,
    DesktopCapabilities,
    DesktopControl,
    DesktopError,
    DesktopImage,
    DesktopInputResult,
    DesktopObservation,
    DesktopWindow,
    DesktopWindows,
)
from agent.protocol.json_value import ThawedJsonValue

REQUEST_LIMIT = 64 * 1024
RESPONSE_LIMIT = 32 * 1024 * 1024


def encode_request(request_id: str, action: str, parameters: Mapping[str, ThawedJsonValue]) -> bytes:
    """生成有界且带版本的私有请求帧。"""
    payload = json.dumps(
        {"v": 1, "id": request_id, "action": action, "parameters": dict(parameters)},
        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(payload) > REQUEST_LIMIT:
        raise DesktopError("request_limit", "Desktop request exceeds size limit")
    return payload


def decode_response(frame: bytes, request_id: str) -> dict[str, ThawedJsonValue]:
    """校验帧、请求关联、版本和成功失败互斥的响应形状。"""
    if len(frame) > RESPONSE_LIMIT or not frame.endswith(b"\n"):
        raise DesktopError("protocol_error", "Oversized or incomplete desktop frame")
    try:
        value: ThawedJsonValue = json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DesktopError("protocol_error", "Invalid desktop JSON frame") from error
    response = _mapping(value)
    if response.get("v") != 1 or type(response.get("v")) is not int or response.get("id") != request_id:
        raise DesktopError("protocol_error", "Desktop version or request id mismatch")
    ok = _boolean(response.get("ok"))
    if ok:
        _fields(response, "v", "id", "ok", "result")
        return _mapping(response["result"])
    _fields(response, "v", "id", "ok", "error")
    failure = _mapping(response["error"])
    _fields(failure, "code", "message")
    raise DesktopError(_string(failure["code"], 64), _string(failure["message"], 4096))


def decode_capabilities(value: dict[str, ThawedJsonValue]) -> DesktopCapabilities:
    """验证握手能力，支持平台差异而不假设两边能力相同。"""
    _fields(value, "protocol", "platform", "actions", "coordinateSpace", "captureSupported", "inputSupported")
    if _integer(value["protocol"]) != 1:
        raise DesktopError("protocol_error", "Unsupported desktop protocol")
    platform = _string(value["platform"], 16)
    if platform not in {"windows", "macos"}:
        raise DesktopError("protocol_error", "Unknown desktop platform")
    actions = tuple(_string(item, 32) for item in _array(value["actions"], 32))
    if len(set(actions)) != len(actions) or not {"list_windows", "observe", "validate_observation"}.issubset(actions):
        raise DesktopError("protocol_error", "Desktop observation capabilities missing or duplicated")
    input_supported = _boolean(value["inputSupported"])
    input_actions = set(actions) & {"activate", "input"}
    if input_actions != ({"activate", "input"} if input_supported else set()):
        raise DesktopError("protocol_error", "Desktop input capabilities are inconsistent")
    return DesktopCapabilities(platform, actions, _coordinate_space(value["coordinateSpace"]),
                               _boolean(value["captureSupported"]), input_supported)


def decode_input_result(value: dict[str, ThawedJsonValue]) -> DesktopInputResult:
    """验证输入提交事实，不把系统接受事件等同于应用完成业务操作。"""
    _fields(value, "eventsSubmitted", "requiresObservation")
    count = _integer(value["eventsSubmitted"])
    if count > 10000 or not _boolean(value["requiresObservation"]):
        raise DesktopError("protocol_error", "Invalid desktop input result")
    return DesktopInputResult(count, True)


def decode_observation_validated(value: dict[str, ThawedJsonValue]) -> None:
    """验证观察校验操作的空成功结果，拒绝额外字段。"""
    _fields(value)


def decode_windows(value: dict[str, ThawedJsonValue]) -> DesktopWindows:
    """把已验证的窗口枚举转换为不可变端口值。"""
    _fields(value, "windows", "truncated")
    windows: list[DesktopWindow] = []
    for raw in _array(value["windows"], 128):
        window = _mapping(raw)
        _fields(window, "window", "title", "pid", "bounds", "displayScale", "minimized")
        windows.append(DesktopWindow(
            _string(window["window"], 64), _string(window["title"], 1024),
            _integer(window["pid"], minimum=1), _bounds(window["bounds"]),
            _positive(window["displayScale"]), _boolean(window["minimized"]),
        ))
    if len({item.reference for item in windows}) != len(windows):
        raise DesktopError("protocol_error", "Duplicate desktop window references")
    return DesktopWindows(tuple(windows), _boolean(value["truncated"]))


def decode_observation(value: dict[str, ThawedJsonValue], expected_window: str) -> DesktopObservation:
    """验证观察与窗口的绑定、树形关系和图片头部。"""
    _fields(value, "window", "observation", "capturedAt", "bounds", "displayScale", "coordinateSpace", "tree", "image")
    if value["window"] != expected_window:
        raise DesktopError("protocol_error", "Observation belongs to a different window")
    tree = _mapping(value["tree"])
    _fields(tree, "nodes", "truncated", "error")
    controls: list[DesktopControl] = []
    for raw in _array(tree["nodes"], 256):
        node = _mapping(raw)
        _fields(node, "index", "parent", "name", "role", "automationId", "enabled", "offscreen", "password", "bounds")
        index = _integer(node["index"])
        parent = _integer(node["parent"], minimum=-1)
        if index != len(controls) or parent >= index or (index > 0 and parent < 0):
            raise DesktopError("protocol_error", "Invalid desktop control hierarchy")
        controls.append(DesktopControl(index, parent, _string(node["name"], 1024),
            _string(node["role"], 128), _string(node["automationId"], 1024),
            _boolean(node["enabled"]), _boolean(node["offscreen"]), _boolean(node["password"]),
            None if node["bounds"] is None else _bounds(node["bounds"])))
    tree_error = None if tree["error"] is None else _string(tree["error"], 128)
    return DesktopObservation(expected_window, _string(value["observation"], 64),
        _string(value["capturedAt"], 64), _bounds(value["bounds"]), _positive(value["displayScale"]),
        _coordinate_space(value["coordinateSpace"]), tuple(controls), _boolean(tree["truncated"]),
        tree_error, None if value["image"] is None else _image(value["image"]))


def _image(value: ThawedJsonValue) -> DesktopImage:
    """验证 PNG/JPEG 的格式、实际尺寸、完整性和编码预算。"""
    image = _mapping(value)
    _fields(image, "mimeType", "width", "height", "data")
    width = _integer(image["width"], minimum=1)
    height = _integer(image["height"], minimum=1)
    mime = image["mimeType"]
    if mime not in ("image/png", "image/jpeg") or width * height > 16000000:
        raise DesktopError("protocol_error", "Unsupported desktop image")
    try:
        content = base64.b64decode(_string(image["data"], 12 * 1024 * 1024), validate=True)
    except (ValueError, binascii.Error) as error:
        raise DesktopError("protocol_error", "Invalid desktop image encoding") from error
    if len(content) > 8 * 1024 * 1024:
        raise DesktopError("protocol_error", "Desktop image exceeds size limit")
    try:
        with Image.open(io.BytesIO(content)) as decoded:
            expected_format = "PNG" if mime == "image/png" else "JPEG"
            if decoded.format != expected_format or decoded.size != (width, height):
                raise DesktopError("protocol_error", "Desktop image dimensions or format mismatch")
            decoded.verify()
        if mime == "image/jpeg":
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise DesktopError("protocol_error", "Invalid desktop image") from error
    return DesktopImage(width, height, content, "image/png" if mime == "image/png" else "image/jpeg")


def _unique_fields(pairs: list[tuple[str, ThawedJsonValue]]) -> dict[str, ThawedJsonValue]:
    """拒绝 JSON 重复键，避免不同解析器产生不同语义。"""
    result: dict[str, ThawedJsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _fields(value: dict[str, ThawedJsonValue], *names: str) -> None:
    """拒绝未声明或缺失字段。"""
    if set(value) != set(names):
        raise DesktopError("protocol_error", "Unknown or missing desktop fields")


def _mapping(value: ThawedJsonValue) -> dict[str, ThawedJsonValue]:
    """收窄外部 JSON 对象。"""
    if not isinstance(value, dict):
        raise DesktopError("protocol_error", "Expected desktop object")
    return value


def _array(value: ThawedJsonValue, limit: int) -> list[ThawedJsonValue]:
    """收窄有界 JSON 数组。"""
    if not isinstance(value, list) or len(value) > limit:
        raise DesktopError("protocol_error", "Invalid desktop array")
    return value


def _string(value: ThawedJsonValue, limit: int) -> str:
    """收窄有界 JSON 字符串。"""
    if not isinstance(value, str) or len(value) > limit:
        raise DesktopError("protocol_error", "Invalid desktop string")
    return value


def _boolean(value: ThawedJsonValue) -> bool:
    """拒绝使用整数代替布尔字段。"""
    if not isinstance(value, bool):
        raise DesktopError("protocol_error", "Expected desktop boolean")
    return value


def _integer(value: ThawedJsonValue, *, minimum: int = 0) -> int:
    """收窄具有下界的整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DesktopError("protocol_error", "Invalid desktop integer")
    return value


def _number(value: ThawedJsonValue) -> float:
    """拒绝非有限坐标与布尔值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopError("protocol_error", "Invalid desktop coordinate")
    try:
        number = float(value)
    except OverflowError as error:
        raise DesktopError("protocol_error", "Desktop coordinate overflows") from error
    if not math.isfinite(number):
        raise DesktopError("protocol_error", "Invalid desktop coordinate")
    return number


def _positive(value: ThawedJsonValue) -> float:
    """校验正数缩放比例。"""
    number = _number(value)
    if number <= 0:
        raise DesktopError("protocol_error", "Invalid desktop scale")
    return number


def _bounds(value: ThawedJsonValue) -> DesktopBounds:
    """校验坐标和非负尺寸。"""
    bounds = _mapping(value)
    _fields(bounds, "x", "y", "width", "height")
    width, height = _number(bounds["width"]), _number(bounds["height"])
    if width < 0 or height < 0:
        raise DesktopError("protocol_error", "Negative desktop dimensions")
    return DesktopBounds(_number(bounds["x"]), _number(bounds["y"]), width, height)


def _coordinate_space(value: ThawedJsonValue) -> str:
    """区分 Windows 物理像素与 macOS 屏幕逻辑点。"""
    name = _string(value, 32)
    if name not in {"physical_screen_pixels", "logical_screen_points"}:
        raise DesktopError("protocol_error", "Unknown desktop coordinate space")
    return name


if __name__ == '__main__':
    pass
