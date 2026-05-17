# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent


class RepairSteps(NativeCodingComponent):
    """校验模型提出的修复步骤，本模块不直接调用模型。"""

    ALLOWED_REPAIR_TOOLS = {
        "workspace_apply_unified_patch",
        "workspace_apply_patch",
        "workspace_read_file",
        "shell_exec"
    }
    ALLOWED_MODIFY_TOOLS = {
        "workspace_apply_unified_patch",
        "workspace_apply_patch"
    }

    def validate_repair_steps(self, steps: typing.Any) -> dict[str, typing.Any]:
        errors: list[str] = []
        if not isinstance(steps, list) or not steps:
            return {"ok": False, "errors": ["steps_empty_or_not_list"]}

        modify_count = 0
        verify_count = 0
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"step_{index}:not_object")
                continue
            tool = str(step.get("tool") or "")
            args = step.get("args")
            if tool not in self.ALLOWED_REPAIR_TOOLS:
                errors.append(f"step_{index}:tool_not_allowed:{tool}")
            if not isinstance(args, dict):
                errors.append(f"step_{index}:args_not_object")
            if tool in self.ALLOWED_MODIFY_TOOLS:
                modify_count += 1
            if tool == "shell_exec":
                verify_count += 1

        if modify_count < 1:
            errors.append("missing_patch_step")
        if verify_count < 1:
            errors.append("missing_verify_step")

        return {
            "ok"           : not errors,
            "errors"       : errors,
            "step_count"   : len(steps),
            "modify_count" : modify_count,
            "verify_count" : verify_count
        }


if __name__ == '__main__':
    pass
