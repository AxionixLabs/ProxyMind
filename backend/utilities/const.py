#   ____                _
#  / ___|___  _ __  ___| |_
# | |   / _ \| '_ \/ __| __|
# | |__| (_) | | | \__ \ |_
#  \____\___/|_| |_|___/\__|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

# ========【应用基础信息】========
APP_ITEM    = r"HelixAI"
APP_NAME    = r"helix"
APP_DESC    = r"Helix"
APP_CN      = r"双螺旋"
APP_VERSION = r"1.0.0"
APP_YEAR    = r"2026"
APP_LICENSE = r"Proprietary License"
CHARSET     = r"UTF-8"
IGNORE      = r"ignore"

AUTHOR      = r"AceKeppel"
EMAIL       = r"AceKeppel@outlook.com"
APP_URL     = r"https://github.com/PlaxtonFlarion/SoftwareCenter"

PUBLISHER   = f"{APP_DESC} Technologies Inc."
COPYRIGHT   = f"Copyright (C) {APP_YEAR} {APP_DESC}. All rights reserved."

DECLARE = f"""\
[bold][bold #00D7AF]>>> {APP_DESC} :: {APP_CN} <<<[/]
[bold #FF8787]Copyright (C)[/] {APP_YEAR} {APP_DESC}. All rights reserved.
Version [bold #FFD75F]{APP_VERSION}[/] :: Licensed software. Authorization required.
{'-' * 59}
"""

# ========【日志与显示设置】========
PRINT_HEAD   = f"[bold #EEEEEE]{APP_DESC} ::[/]"
PRINT_FORMAT = f"<level>{{message}}</level>"

# ========【错误提示】========
SKIP = {
    "text"        : "跳过：不在 matrix 目标列表",
    "attachments" : [],
    "data"        : {"skipped": True}
}

# ========【错误提示】========
HINT_STOP    = "【不可重试】确定性失败：立即终止工作流（不要重试、不要继续调用后续工具、不要尝试替代方案）。"
HINT_STOP_I  = (
    "【不可继续/不可重试】这是服务内部状态未就绪或未回填。"
    "请停止当前流程，不要继续调用后续工具。"
    "请由使用者检查上游任务是否完成（例如录屏/采集是否结束并落盘）、输出目录是否生成、以及任务日志。"
)
CODE_EXC     = "EXC"
CODE_PATH    = "PATH_INVALID"
CODE_PORT    = "PORT_BUSY"
CODE_EMPTY   = "EMPTY_INPUT"
CODE_EMPTY_I = "EMPTY_STATE"
CODE_SUBPROC = "SUBPROC_ERROR"

# ========【服务授权】========
# openssl rand -base64 64 | tr '+/' '-_' | tr -d '=\n'
MASTER   = r"7zUyfFya8Av0_ixhxKgLeGfVkKF0xy5qQw9pGnEobEZx6kgjKmrUVHiUvdlibNKwybf_H1vRt7_-2PfMLmtACA%"
ISSUER   = r"https://auth.helix.local/issuer"
AUDIENCE = r"helix-mcp-api"
RS_URL   = r"http://127.0.0.1:3333/mcp"


if __name__ == '__main__':
    pass
