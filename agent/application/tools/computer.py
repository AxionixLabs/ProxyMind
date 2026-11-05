# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import base64

from agent.application.tools.authorization import ToolTurnInterrupted
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.desktop import (
    ActivateDesktopWindow,
    DesktopBounds,
    DesktopClick,
    DesktopCommand,
    DesktopDrag,
    DesktopError,
    DesktopExecutionPort,
    DesktopInput,
    DesktopInputResult,
    DesktopKey,
    DesktopMove,
    DesktopObservation,
    DesktopOperation,
    DesktopPoint,
    DesktopResult,
    DesktopScope,
    DesktopScroll,
    DesktopText,
    DesktopWindows,
    ListDesktopWindows,
    ObserveDesktopWindow,
    ValidateDesktopObservation,
)
from agent.protocol.json_value import ThawedJsonValue


_FIELDS = {
    "list_windows": (), "reset": (), "activate": ("window",),
    "observe": ("window", "screenshot"), "validate_observation": ("window", "observation"),
    "click": ("window", "observation", "target", "button", "count"),
    "move": ("window", "observation", "target", "duration_ms"),
    "drag": ("window", "observation", "start", "end", "duration_ms"),
    "scroll": ("window", "observation", "target", "delta_x", "delta_y"),
    "press_key": ("window", "observation", "keys"), "type_text": ("window", "observation", "text"),
}
_KEYS = frozenset((*"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", *(f"F{n}" for n in range(1, 25)),
                   "CTRL", "ALT", "SHIFT", "WIN", "ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "DELETE",
                   "HOME", "END", "LEFT", "UP", "RIGHT", "DOWN", "PAGEUP", "PAGEDOWN"))
_POINT_SCHEMA: dict[str, ThawedJsonValue] = {
    "type": "object", "properties": {"x": {"type": "integer", "minimum": 0, "maximum": 16000000},
                                     "y": {"type": "integer", "minimum": 0, "maximum": 16000000}},
    "required": ["x", "y"], "additionalProperties": False,
}
COMPUTER_INPUT_SCHEMA: dict[str, ThawedJsonValue] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_FIELDS)},
        "window": {"type": "string", "minLength": 1, "maxLength": 64},
        "screenshot": {"type": "boolean"},
        "observation": {"type": "string", "minLength": 1, "maxLength": 64},
        "target": {"oneOf": [_POINT_SCHEMA, {"type": "object", "properties": {
            "element": {"type": "integer", "minimum": 0, "maximum": 255}},
            "required": ["element"], "additionalProperties": False}]},
        "start": _POINT_SCHEMA, "end": _POINT_SCHEMA,
        "button": {"type": "string", "enum": ["left", "right", "middle"]},
        "count": {"type": "integer", "minimum": 1, "maximum": 2},
        "duration_ms": {"type": "integer", "minimum": 0, "maximum": 1200},
        "delta_x": {"type": "integer", "minimum": -20, "maximum": 20},
        "delta_y": {"type": "integer", "minimum": -20, "maximum": 20},
        "keys": {"type": "array", "minItems": 1, "maxItems": 4, "uniqueItems": True,
                 "items": {"type": "string", "enum": sorted(_KEYS)}},
        "text": {"type": "string", "minLength": 1, "maxLength": 2048},
    },
    "required": ["action"],
    "additionalProperties": False,
    "oneOf": [
        {"properties": {"action": {"const": action}}, "required": ["action", *fields], "maxProperties": len(fields) + 1}
        for action, fields in _FIELDS.items()
    ],
}


def _reference(arguments: dict[str, ThawedJsonValue], name: str) -> str:
    """收窄有界的不透明引用，不接受空值或隐式类型转换。"""
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError(f"{name} must be a non-empty reference of at most 64 characters")
    return value


def _command(arguments: dict[str, ThawedJsonValue]) -> DesktopCommand | None:
    """按动作验证完整字段集合，None 仅代表关闭会话的 reset。"""
    action = arguments.get("action")
    if not isinstance(action, str) or action not in _FIELDS or set(arguments) != {"action", *_FIELDS[action]}:
        raise ValueError("Unsupported computer action or fields")
    if action in ("list_windows", "reset") and set(arguments) == {"action"}:
        return ListDesktopWindows() if action == "list_windows" else None
    if action == "observe" and set(arguments) == {"action", "window", "screenshot"}:
        screenshot = arguments["screenshot"]
        if not isinstance(screenshot, bool):
            raise ValueError("screenshot must be a boolean")
        return ObserveDesktopWindow(_reference(arguments, "window"), screenshot)
    if action == "validate_observation" and set(arguments) == {"action", "window", "observation"}:
        return ValidateDesktopObservation(_reference(arguments, "window"), _reference(arguments, "observation"))
    if action == "activate":
        return ActivateDesktopWindow(_reference(arguments, "window"))
    return DesktopInput(_reference(arguments, "window"), _reference(arguments, "observation"), _operation(action, arguments))


def _integer(value: ThawedJsonValue, minimum: int, maximum: int) -> int:
    """限制整数字段，不把布尔值当作坐标或次数。"""
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"Expected an integer in {minimum}..{maximum}")
    return value


def _point(value: ThawedJsonValue) -> DesktopPoint:
    """只接受当前观察截图中的有界像素坐标。"""
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise ValueError("Expected screenshot coordinates {x, y}")
    return DesktopPoint(_integer(value["x"], 0, 16000000), _integer(value["y"], 0, 16000000))


def _operation(action: str, arguments: dict[str, ThawedJsonValue]) -> DesktopOperation:
    """把完整输入参数转换为具名值，拒绝未定义目标与按键。"""
    if action == "click":
        raw = arguments["target"]
        target = _integer(raw["element"], 0, 255) if isinstance(raw, dict) and set(raw) == {"element"} else _point(raw)
        button = arguments["button"]
        if button == "left" or button == "right" or button == "middle":
            return DesktopClick(target, button, _integer(arguments["count"], 1, 2))
        raise ValueError("Invalid mouse button")
    if action == "move":
        return DesktopMove(_point(arguments["target"]), _integer(arguments["duration_ms"], 0, 1200))
    if action == "drag":
        return DesktopDrag(_point(arguments["start"]), _point(arguments["end"]), _integer(arguments["duration_ms"], 50, 1200))
    if action == "scroll":
        x, y = _integer(arguments["delta_x"], -20, 20), _integer(arguments["delta_y"], -20, 20)
        if x == y == 0:
            raise ValueError("Scroll requires a nonzero delta")
        return DesktopScroll(_point(arguments["target"]), x, y)
    if action == "press_key":
        values = arguments["keys"]
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise ValueError("Expected one to four named keys")
        keys: list[str] = []
        for value in values:
            if not isinstance(value, str) or value not in _KEYS or value in keys:
                raise ValueError("Unsupported or duplicate key")
            keys.append(value)
        return DesktopKey(tuple(keys))
    if action == "type_text":
        value = arguments["text"]
        if not isinstance(value, str) or not value or "\0" in value or len(value.encode("utf-16-le")) > 4096:
            raise ValueError("Expected 1..2048 UTF-16 units without NUL")
        return DesktopText(value)
    raise ValueError("Unsupported input action")


async def _authorize(
    runtime: ToolHandlerContext,
    arguments: dict[str, ThawedJsonValue],
    coordinator: ApprovalCoordinatorPort | None,
) -> None:
    """使用本地桌面审批类别；启用开关和文件系统写权限不单独构成动作批准。"""
    turn = runtime.turn_context
    call_id = runtime.call_id
    if not call_id or not turn.turn_id:
        raise DesktopError("approval_identity_missing", "Desktop call and turn identity are required")
    if turn.permissions.preset == "full-access":
        return
    if turn.permissions.approval_policy == "never":
        raise DesktopError("approval_required", "Desktop operation requires approval under the current policy")
    if coordinator is None:
        raise DesktopError("approval_unavailable", "Desktop approval coordinator is unavailable")
    outcome = await coordinator.request_outcome({
        "kind": "computer", "tool": "computer", "id": f"desktop:{turn.sid}:{turn.turn_id}:{call_id}",
        "call_id": call_id, "sid": turn.sid, "turn_id": turn.turn_id,
        "cwd": turn.cwd, "environment_id": "desktop", "arguments": arguments,
        "available_decisions": ["accept", "decline", "cancel"],
        "justification": f"执行桌面操作 {arguments['action']}，目标和输入参数以本次请求为准。",
        "agent_id": turn.agent.agent_id, "agent_type": turn.agent.agent_type, "agent_depth": turn.agent.depth,
    })
    if outcome.decision == "cancel":
        if runtime.interrupt_turn is not None and await runtime.interrupt_turn(call_id):
            raise ToolTurnInterrupted("Desktop approval cancelled the turn")
        raise DesktopError("approval_cancelled", "Desktop operation was cancelled")
    if outcome.decision != "accept":
        raise DesktopError("approval_denied", "Desktop operation was not approved")


def _bounds(value: DesktopBounds) -> dict[str, ThawedJsonValue]:
    """把类型化坐标投影为工具结果中的具名矩形。"""
    return {"x": value.x, "y": value.y, "width": value.width, "height": value.height}


def _data(result: DesktopResult) -> dict[str, ThawedJsonValue]:
    """投影观察元数据，图像由附件路径单独交付。"""
    if isinstance(result, DesktopInputResult):
        return {"eventsSubmitted": result.events_submitted, "requiresObservation": result.requires_observation}
    if isinstance(result, DesktopWindows):
        windows: list[ThawedJsonValue] = [{
            "window": value.reference, "title": value.title, "pid": value.pid,
            "bounds": _bounds(value.bounds), "displayScale": value.display_scale, "minimized": value.minimized,
        } for value in result.windows]
        return {"windows": windows, "truncated": result.truncated}
    if isinstance(result, DesktopObservation):
        controls: list[ThawedJsonValue] = [{
            "index": value.index, "parent": value.parent, "name": value.name, "role": value.role,
            "automationId": value.automation_id, "enabled": value.enabled, "offscreen": value.offscreen,
            "password": value.password, "bounds": None if value.bounds is None else _bounds(value.bounds),
        } for value in result.controls]
        return {
            "window": result.window, "observation": result.reference, "capturedAt": result.captured_at,
            "bounds": _bounds(result.bounds), "displayScale": result.display_scale,
            "coordinateSpace": result.coordinate_space,
            "tree": {"nodes": controls, "truncated": result.tree_truncated, "error": result.tree_error},
            "image": None if result.image is None else {"width": result.image.width, "height": result.image.height},
        }
    return {}


def computer_tools(executor: DesktopExecutionPort, *, approval_coordinator: ApprovalCoordinatorPort | None) -> list[ClientTool]:
    """构造单一桌面工具，执行前授权，调用级上下文不跨 Turn 保存。"""

    async def handle(arguments: dict[str, ThawedJsonValue], runtime: ToolHandlerContext) -> LocalToolResult:
        """校验并授权一次桌面观察，使用既有结构化结果和图片附件。"""
        try:
            command = _command(arguments)
        except ValueError as error:
            return client_tool_result(tool="computer", ok=False, text=str(error), data={"error": "invalid_arguments"})
        try:
            if command is None:
                await executor.reset_session(runtime.turn_context.sid)
                result = None
            else:
                await _authorize(runtime, arguments, approval_coordinator)
                turn = runtime.turn_context
                result = await executor.execute(DesktopScope(turn.sid, turn.cwd, turn.permissions), command)
        except DesktopError as error:
            return client_tool_result(tool="computer", ok=False, text=f"{error.code}: {error.detail}",
                                      args=arguments, data={"error": error.code})
        attachments: list[dict[str, ThawedJsonValue]] = []
        if isinstance(result, DesktopObservation) and result.image is not None:
            mime = result.image.mime_type
            extension = "png" if mime == "image/png" else "jpg"
            attachments.append({
                "kind": "image", "mime_type": mime, "filename": f"desktop-window.{extension}", "detail": "original",
                "data_url": f"data:{mime};base64," + base64.b64encode(result.image.content).decode("ascii"),
            })
        return client_tool_result(tool="computer", ok=True, text=f"Desktop {arguments['action']} completed.",
                                  args=arguments, data=_data(result), attachments=attachments)

    return [ClientTool(
        name="computer", input_schema=COMPUTER_INPUT_SCHEMA, handler=handle,
        description=(
            "观察本机桌面窗口。list_windows 枚举；observe 必须传 window 和 screenshot 布尔值；"
            "validate_observation 校验 window 与 observation；reset 关闭本会话桌面进程并作废引用。"
            "activate 显式激活 window；click/move/drag/scroll/press_key/type_text 执行输入，必须传 window、最新 observation。"
            "每次输入或激活后必须重新 observe 验证应用结果；eventsSubmitted 只说明系统收到了事件。"
            "click 的 target 为 {element:控件index} 或 {x,y}；其他指针动作使用截图像素坐标，必须来自 screenshot=true 的观察。"
            "选择控件必须检查 enabled=true、offscreen=false、password=false；不可点击禁用子控件，应观察并选择实际可交互的父控件。"
            "激活失败后不得继续按键；先解决前台焦点，观察仍可只读执行。"
            "click 必填 button(left/right/middle)、count(1/2)；move/drag 必填 duration_ms(0..1200，drag至少50)，drag 使用 start/end。"
            "scroll 必填 delta_x/delta_y(-20..20，正数向右/上)；press_key 使用大写 keys 数组，如 ['CTRL','A']；type_text 使用 text。"
            "用户指定 computer/UI 操作时使用本工具完成，不改用 shell 操作文件。"
            "引用不能跨会话使用，观察完成后有效期 120 秒；窗口或目标控件变化后须重新观察。失败输入不得自动重放。"
            "桌面会话在同一 sid 内跨调用保持；蓝光和代理指针只在 activate 或真实输入动作期间显示，"
            "动作结束立即关闭，list_windows/observe/validate_observation/reset 与模型思考期间不显示。"
            "input_busy 表示动作开始前检测到按键占用，未执行输入；松开后重新观察，无需 reset。"
            "用户鼠标键盘介入会停止输入；只有用户明确要求继续后才 reset 重建，禁止自行重试。reset 也可中止活动操作。"
            "JS 示例：var shot = await host.tool('computer', {action:'observe',window:windowRef,screenshot:true});"
            "console.log(shot.data); await host.emitImage(shot); 所有动作的结构化结果均读取 result.data，"
            "不要读取 result.output.tree，不要丢弃工具结果外层对象。坐标动作使用 shot.data.observation；"
            "不要再取 screenshot=false 的观察替换它。控件 index 仅供 click，move 不接受 element。"
            "JS 顶层使用可重复声明的 var，辅助函数先定义后调用；不预写包含未知界面的连续操作。"
        ),
        meta={"hidden": False, "domain": "client", "class": "action"},
    )]


if __name__ == '__main__':
    pass
