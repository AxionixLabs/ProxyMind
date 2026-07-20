# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

CHAT_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("你", "好"),
    ("你好", "，请介绍一下你自己"),
    ("你好呀", "，请介绍一下你自己"),
    ("嗨", "，请介绍一下你自己"),
    ("哈喽", "，请介绍一下你自己"),
    ("hi", "，请介绍一下你自己"),
    ("hello", "，请介绍一下你自己"),
    ("在吗", "，说说你能做什么"),
    ("你在吗", "，说说你能做什么"),
    ("测试", "一下你能做什么"),
    ("开始", "吧"),
    ("继续", "刚才的话题"),
    ("介绍一下你自己", ""),
    ("说说你能做什么", ""),
    ("你是", "谁"),
    ("你能", "做什么"),
)

GENERAL_TASK_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("分析", "一下这个问题"),
    ("解释", "一下这个问题"),
    ("总结", "一下这个内容"),
    ("整理", "一下这个内容"),
    ("提炼", "重点"),
    ("精简", "一下这个内容"),
    ("优化", "一下这段内容"),
    ("润色", "一下这段话"),
    ("改写", "得更自然一点"),
    ("给个", "方案"),
    ("给我", "一个执行方案"),
    ("拆解", "执行步骤"),
    ("列出", "风险点"),
    ("评估", "是否可行"),
    ("对比", "两种方案的优缺点"),
    ("复盘", "这个问题"),
    ("为什么", "会这样"),
    ("有没有", "更好的方案"),
)

CODING_AGENT_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("代码", "审查并修改实现"),
    ("仓库", "扫描代码结构"),
    ("审查", "当前改动并指出风险"),
    ("评审", "当前改动并指出风险"),
    ("改动", "检查当前 diff"),
    ("差异", "检查当前 diff"),
    ("diff", "检查当前改动并指出风险"),
    ("当前", "改动"),
    ("当前改动", ""),
    ("排查", "失败原因并给出修复"),
    ("定位", "问题根因"),
    ("复现", "问题并定位根因"),
    ("修复", "问题并运行验证"),
    ("补丁", "最小改动并验证"),
    ("验证", "修改结果并汇总结论"),
    ("构建", "项目并修复失败"),
    ("打包", "构建产物并排查失败"),
    ("启动", "项目并排查启动失败原因"),
    ("测试", "当前项目并定位失败原因"),
    ("单测", "运行并修复失败用例"),
    ("回归", "运行相关验证并汇总结论"),
    ("类型", "检查并修复类型问题"),
    ("lint", "检查并修复代码风格问题"),
    ("格式化", "代码并确认 diff"),
    ("依赖", "检查依赖和版本问题"),
    ("调用链", "跟踪并定位问题"),
    ("接口", "检查调用链和返回结果"),
    ("日志", "分析异常原因"),
    ("报错", "分析原因并给出修复方案"),
    ("性能", "分析瓶颈并给出优化方案"),
    ("内存", "检查泄漏风险并给出修复"),
    ("发布", "检查发布配置和潜在风险"),
    ("重构", "在保持行为不变的前提下优化实现"),
)

GHOST_TEMPLATES: dict[str, tuple[tuple[str, str], ...]] = {
    "chat"    : CHAT_TEMPLATES,
    "general" : GENERAL_TASK_TEMPLATES,
    "coding"  : CODING_AGENT_TEMPLATES
}

GHOST_MAX_INPUT_CHARS: int = 12

GHOST_CODE_MARKERS: tuple[str, ...] = (
    "npm ",
    "git ",
    "pnpm ",
    "yarn ",
    "python ",
    "curl ",
    "http://",
    "https://",
    "/",
    "\\",
    "{",
    "}",
    "=",
    ";",
    ".py",
    ".ts",
    ".js",
)

GHOST_OBJECT_MARKERS: tuple[str, ...] = (
    "这个接口",
    "这个日志",
    "这个报错",
    "这个文件",
    "这段代码",
    "这个函数",
    "这个问题",
    "这个需求",
)


def should_apply_ghost_prompt(text: str) -> bool:
    """只允许弱意图短输入触发 ghost。"""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped != text:
        return False
    if len(stripped) > GHOST_MAX_INPUT_CHARS:
        return False
    if any(marker in stripped for marker in GHOST_CODE_MARKERS):
        return False

    return not any(marker in stripped for marker in GHOST_OBJECT_MARKERS)


def iter_ghost_templates() -> tuple[tuple[str, str], ...]:
    """按场景优先级返回所有显式 ghost 模板。"""
    return (
        GHOST_TEMPLATES["chat"] + GHOST_TEMPLATES["general"] + GHOST_TEMPLATES["coding"]
    )


def apply_ghost_prompt(text: str, templates: tuple[tuple[str, str], ...]) -> str:
    """命中模板时返回完整候选，否则回退原文。"""
    raw = text

    if not should_apply_ghost_prompt(text):
        return raw

    for prefix, suffix in templates:
        candidate = f"{prefix}{suffix}"
        if text == prefix or text == candidate:
            return candidate

    for prefix, suffix in templates:
        candidate = f"{prefix}{suffix}"
        if candidate.startswith(text) and candidate != text:
            return candidate

    return raw


if __name__ == '__main__':
    pass
