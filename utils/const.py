#   ____                _
#  / ___|___  _ __  ___| |_
# | |   / _ \| '_ \/ __| __|
# | |__| (_) | | | \__ \ |_
#  \____\___/|_| |_|___/\__|
#

APP_ITEM    = r"ProxyMind"
APP_NAME    = r"mind"
APP_DESC    = r"Mind"
APP_CN      = r"代理思维"
APP_VERSION = r"1.0.0"
APP_YEAR    = r"2026"
APP_LICENSE = r"Proprietary License"
CHARSET     = r"UTF-8"
IGNORE      = "ignore"

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

# ========【路径与资源配置】========
SCHEMATIC = r"schematic"
SUPPORTS  = r"supports"
STRUCTURE = r"Structure"

# ========【日志与显示设置】========
PRINT_HEAD = f"[bold #EEEEEE]{APP_DESC} ::[/]"
SHOW_LEVEL = r"WARNING"
NOTE_LEVEL = r"INFO"

SUC = f"[bold #FFFFFF on #32CD32]"
WRN = f"[bold #000000 on #FFFF00]"
ERR = f"[bold #FFFFFF on #FF6347]"

PRINT_FORMAT = f"<level>{{level: <8}}</level> | <level>{{message}}</level>"


if __name__ == '__main__':
    pass
