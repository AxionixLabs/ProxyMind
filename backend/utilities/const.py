#   ____                _
#  / ___|___  _ __  ___| |_
# | |   / _ \| '_ \/ __| __|
# | |__| (_) | | | \__ \ |_
#  \____\___/|_| |_|___/\__|
#

# ========【应用基础信息】========
APP_ITEM    = r"HelixServer"
APP_NAME    = r"helix"
APP_DESC    = r"Helix"
APP_CN      = r"双螺旋"
APP_VERSION = r"1.0.0"
APP_YEAR    = r"2026"
APP_LICENSE = r"Proprietary License"
CHARSET     = r"UTF-8"

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
PRINT_HEAD   = f"🧬"
PRINT_FORMAT = f"<level>{{message}}</level>"


if __name__ == '__main__':
    pass
