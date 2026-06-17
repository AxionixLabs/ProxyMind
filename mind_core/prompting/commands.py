# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.completion import (
    Completer,
    Completion
)
from .skills import (
    is_skill_token,
    skill_completions
)


class SlashCommandCompleter(Completer):
    """命令补全视图。"""

    COMMANDS: tuple[dict[str, str], ...] = (
        {"text": "/chat", "display": "/chat", "meta": "切换到 Chat 模式"},
        {"text": "/fast", "display": "/fast", "meta": "切换到 Fast 模式"},
        {"text": "/plan", "display": "/plan", "meta": "切换到 Plan 模式"},
        {"text": "/xtra", "display": "/xtra", "meta": "切换到 Xtra 模式"},
        {"text": "/help", "display": "/help", "meta": "查看帮助"},
        {"text": "/h", "display": "/h", "meta": "查看帮助"},
        {"text": "$", "display": "/skills", "meta": "打开 skills 列表", "match": "/skills"},
        {"text": "/license", "display": "/license", "meta": "查看授权"},
        {"text": "/lic", "display": "/lic", "meta": "查看授权"},
        {"text": "/quit", "display": "/quit", "meta": "退出会话"},
        {"text": "/q", "display": "/q", "meta": "退出会话"},
        {"text": "/model ", "display": "/model", "meta": "输入模型名"},
        {"text": "/apikey ", "display": "/apikey", "meta": "输入 API Key"},
        {"text": "/attach ", "display": "/attach", "meta": "添加本轮待发送附件"},
        {"text": "/attachments", "display": "/attachments", "meta": "查看待发送附件"},
        {"text": "/detach ", "display": "/detach", "meta": "移除待发送附件"},
        {"text": "/attach-clear", "display": "/attach-clear", "meta": "清空待发送附件"},
        {"text": "/reboot", "display": "/reboot", "meta": "重启本地后台服务"}
    )

    TOP_LEVEL: tuple[str, ...] = (
        "/chat",
        "/fast",
        "/plan",
        "/xtra",
        "/help",
        "/skills",
        "/license",
        "/quit",
        "/model",
        "/apikey",
        "/attach",
        "/attachments",
        "/detach",
        "/attach-clear",
        "/reboot"
    )

    def get_completions(self, document, complete_event):
        """根据当前输入内容生成补全项。"""
        text = document.text_before_cursor
        stripped = text.lstrip()

        if is_skill_token(text):
            yield from skill_completions(text)
            return

        if not stripped.startswith("/"):
            return

        token = stripped.splitlines()[-1]
        if " " in token and not token.startswith(("/model", "/apikey", "/attach", "/detach")):
            return

        if token == "/":
            candidates = [
                item for item in self.COMMANDS if item["display"] in self.TOP_LEVEL
            ]
            visible_limit = len(self.TOP_LEVEL)
        else:
            candidates = [
                item for item in self.COMMANDS
                if item["display"].startswith(token)
                or item["text"].startswith(token)
                or str(item.get("match") or "").startswith(token)
            ]
            visible_limit = 7

        for item in candidates[:visible_limit]:
            yield Completion(
                item["text"],
                start_position=-len(token),
                display=item["display"],
                display_meta=item["meta"]
            )


if __name__ == '__main__':
    pass
