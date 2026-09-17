# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.mcp_elicitation import (
    ElicitationField,
    ElicitationRequest,
    ElicitationResponse,
    FormValue,
)
from frontends.tui.contracts.menu import (
    MenuEmptyAcceptAction,
    MenuOption,
    MenuRequest,
    MenuTextInputMode,
)

SelectMenu: typing.TypeAlias = typing.Callable[[MenuRequest], typing.Awaitable[str | None]]


def _value_text(value: FormValue | None) -> str:
    """把表单局部值投影为可编辑文本，不写入会话或命令历史。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return ", ".join(value)
    return str(value)


async def _edit_field(field: ElicitationField, current: FormValue | None, select: SelectMenu) -> FormValue | None:
    """编辑一个普通字段，Escape 保留原值，无效输入留在本字段重试。"""
    error = ""
    text = _value_text(current)
    selected = set(current) if isinstance(current, tuple) else set()
    constraints = [field.description, f"Type: {field.kind}"]
    if field.kind == "string":
        constraints.append(f"Length: {field.min_length}–{field.max_length}")
    elif field.kind == "array":
        constraints.append(f"Selections: {field.min_items}–{field.max_items}")
    if field.minimum is not None:
        constraints.append(f"Minimum: {field.minimum:g}")
    if field.maximum is not None:
        constraints.append(f"Maximum: {field.maximum:g}")
    if field.format is not None:
        constraints.append(f"Format: {field.format}")
    while True:
        if field.kind == "array":
            options = tuple(MenuOption(f"choice:{index}", f"{'[x]' if value in selected else '[ ]'} {label}")
                for index, (value, label) in enumerate(field.choices)) + (MenuOption("done", "Done"),)
        elif field.kind == "boolean":
            options = (MenuOption("true", "Yes"), MenuOption("false", "No"))
        elif field.choices:
            options = tuple(MenuOption(f"choice:{index}", label) for index, (_value, label) in enumerate(field.choices))
        else:
            options = ()
        if options and not field.required:
            options += (MenuOption("unset", "Leave unset"),)
        result = await select(MenuRequest(
            title=field.title,
            body=tuple(constraints),
            status=error,
            options=options,
            text_input_mode=(
                MenuTextInputMode.NONE if options else MenuTextInputMode.MULTILINE if field.kind == "string" else MenuTextInputMode.SINGLE_LINE
            ),
            initial_query=text,
            text_input_max_length=4096,
            text_input_allow_empty=True,
            text_input_preserve_whitespace=field.kind == "string",
            empty_accept_action=MenuEmptyAcceptAction.CANCEL if options else MenuEmptyAcceptAction.SUBMIT_QUERY,
            search_placeholder="Enter a value",
            search_prompt_prefix="  Value: ",
            footer_hint="Enter to confirm · Esc to go back",
            body_wrap=True,
        ))
        if result is None:
            return current
        if options and result == "unset":
            return None
        if field.kind == "array" and result.startswith("choice:"):
            value = field.choices[int(result.partition(":")[2])][0]
            if value in selected:
                selected.remove(value)
            else:
                selected.add(value)
            continue
        try:
            value: FormValue
            if field.kind == "array":
                value = tuple(item for item, _label in field.choices if item in selected)
            elif field.kind == "boolean":
                value = result == "true"
            elif field.choices:
                value = field.choices[int(result.partition(":")[2])][0]
            elif not result and not field.required and field.kind != "string":
                return None
            elif field.kind == "integer":
                text = result
                value = int(result)
            elif field.kind == "number":
                text = result
                value = float(result)
            else:
                text = result
                value = result
            field.validate(value)
            return value
        except (ValueError, OverflowError):
            error = "Enter a value matching this field's constraints."


async def present_elicitation(request: ElicitationRequest, select: SelectMenu) -> ElicitationResponse:
    """展示来源、可修改的最终答案或目标域名，提交、拒绝与取消分别产生终态。"""
    source = f"Requested by {request.server} · Agent {request.invocation.agent_id}"
    if request.url is not None:
        decision = await select(MenuRequest(
            title=f"MCP · {request.server} · Open a website",
            body=(source, request.message,
                f"Destination: {request.url_host}", request.url),
            body_wrap=True,
            options=(
                MenuOption("accept", "Open URL", "Open this address in your browser"), MenuOption("decline", "Decline"), MenuOption("cancel", "Cancel")
            ),
            footer_hint="Enter to submit · Esc to cancel",
        ))
        return ElicitationResponse("accept" if decision == "accept" else "decline" if decision == "decline" else "cancel")
    fields = request.fields or ()
    values = {field.name: field.default for field in fields if field.default is not None}
    error = ""
    while True:
        decision = await select(MenuRequest(
            title=f"MCP · {request.server} · Review information",
            body=(source, request.message),
            body_wrap=True,
            options=tuple(MenuOption(f"field:{index}", field.title + (" *" if field.required else ""),
                (_value_text(values[field.name])[:160] or '""') if field.name in values else "(unset)") for index, field in enumerate(fields))
                + (MenuOption("accept", "Submit", "Send these values to this MCP server"),
                    MenuOption("decline", "Decline"), MenuOption("cancel", "Cancel")),
            status=error,
            footer_hint="Enter to edit or submit · Esc to cancel",
        ))
        if decision is None or decision == "cancel":
            return ElicitationResponse("cancel")
        if decision == "decline":
            return ElicitationResponse("decline")
        if decision == "accept":
            response = ElicitationResponse("accept", tuple(values.items()))
            try:
                request.validate_response(response)
            except ValueError:
                error = "Complete the required fields before submitting."
                continue
            return response
        field = fields[int(decision.partition(":")[2])]
        value = await _edit_field(field, values.get(field.name), select)
        if value is None:
            values.pop(field.name, None)
        else:
            values[field.name] = value
        error = ""


if __name__ == '__main__':
    pass
