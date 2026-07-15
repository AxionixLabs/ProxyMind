# -*- coding: utf-8 -*-
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
PRINT_HEAD   = f"[bold #8B8B8B]{APP_DESC} ::[/]"
PRINT_FORMAT = f"<level>{{message}}</level>"

# ========【错误提示】========
HINT_HLT = "This step may not produce a valid result. Check the input, configuration, and environment before continuing."
CODE_EXC = "EXC"

# ========【运行时配置】========
IDLE_TTL_SEC      = 1800.0
KEEPALIVE_SEC     = 300.0
KEEPALIVE_TIMEOUT = 3.0

# ========【服务授权】========
# openssl rand -base64 64 | tr '+/' '-_' | tr -d '=\n'
MASTER   = r"7zUyfFya8Av0_ixhxKgLeGfVkKF0xy5qQw9pGnEobEZx6kgjKmrUVHiUvdlibNKwybf_H1vRt7_-2PfMLmtACA%"
ISSUER   = r"https://auth.helix.local/issuer"
AUDIENCE = r"helix-mcp-api"
RS_URL   = r"http://127.0.0.1:3333/mcp"


if __name__ == '__main__':
    pass
