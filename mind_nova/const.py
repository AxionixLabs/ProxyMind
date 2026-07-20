# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

APP_ITEM    = r"ProxyMind"
APP_NAME    = r"mind"
APP_DESC    = r"Mind"
APP_CN      = r"代理思维"
APP_VERSION = r"1.1.9"
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

# ========【路径与资源配置】========
LIC_FILE        = f"{APP_NAME}_signature.lic"
SCHEMATIC       = r"schematic"
SUPPORTS        = r"supports"
STRUCTURE       = r"Structure"
SRC_OPERA_PLACE = f"{APP_DESC}_Mix"
SRC_TOTAL_PLACE = f"{APP_DESC}_Report"
PREF            = f"{APP_NAME}_pref.json"
R_TOTAL_TAG     = r"PM"
R_COLLECTION    = f"{APP_DESC}_Collection"
R_RECOVERY      = f"{APP_DESC}_Recovery"
R_LOG_FILE      = f"{APP_NAME}.log"

# ========【日志与显示设置】========
PRINT_HEAD = f"[bold #8B8B8B]{APP_DESC} ::[/]"
OTHER_HEAD = f"{APP_DESC} ::"
SHOW_LEVEL = r"INFO"
NOTE_LEVEL = r"DEBUG"

SUC = f"[bold #FFFFFF on #32CD32]"
WRN = f"[bold #000000 on #FFFF00]"
ERR = f"[bold #FFFFFF on #FF6347]"

PRINT_FORMAT = f"<level>{{level: <8}}</level> | <level>{{message}}</level>"
WRITE_FORMAT = f"{OTHER_HEAD} <green>{{time:YYYY-MM-DD HH:mm:ss.SSS}}</green> | <level>{{level: <8}}</level> | <level>{{message}}</level>"

# ========【运行时配置】========
KEEPALIVE_SEC         = 300.0
KEEPALIVE_TIMEOUT_SEC = 3.0

# ========【服务授权】========
# openssl rand -base64 64 | tr '+/' '-_' | tr -d '=\n'
MASTER   = r"7zUyfFya8Av0_ixhxKgLeGfVkKF0xy5qQw9pGnEobEZx6kgjKmrUVHiUvdlibNKwybf_H1vRt7_-2PfMLmtACA%"
ISSUER   = r"https://auth.helix.local/issuer"
AUDIENCE = r"helix-mcp-api"
BASE_URL = r"http://127.0.0.1:3333"
MCP_ED   = r"/helix/mcp"

# ========【域名管理】========
AGENT_CLIENT_SECRET = "177P81LAw5fdeUp2IRX9q-i6hW9gCPTiBHKzkpSC9tE"
AGENT_ADMIN_SECRET  = "U2d73xNcBFU8Gewr9DKk_-8-048tfosxfFvqiu0v_Wo"

# ========【域名管理】========
DOMAIN = f"https://api.appserverx.com"

# ========【专有服务】========
ATLAS_URL         = f"{DOMAIN}/mind-atlas"
REPORT_OPEN_URL   = f"{DOMAIN}/reports/open"
HEAL_LIC_URL      = f"{DOMAIN}/mind-heal-license"
MANIFEST_URL      = f"{DOMAIN}/mind-manifest"
STREAM_EVENT_URL  = f"{DOMAIN}/events-ingest"
FILE_STREAM_URL   = f"{DOMAIN}/upload"
TOOL_RESULT_URL   = f"{DOMAIN}/tool-result"
TOOL_APPROVAL_URL = f"{DOMAIN}/tool-approval"
STREAM_CHAT_URL   = f"{DOMAIN}/mind-chat"
STREAM_HEAL_URL   = f"{DOMAIN}/mind-heal"

# ========【应用授权】========
BOOTSTRAP_URL      = f"{DOMAIN}/bootstrap"
TEMPLATE_META_URL  = f""
BUSINESS_CASE_URL  = f""
SPEECH_META_URL    = f""
SPEECH_VOICE_URL   = f""
GLOBAL_CF_URL      = f"{DOMAIN}/global-configuration"
PREDICT_URL        = r""
TOOLKIT_META_URL   = r""
MODEL_META_URL     = r""
X_TEMPLATE_VERSION = f""
SHARED_SECRET      = r"xosspWbJNo9hUjR4OceTuSLshorCn0IXucTKO0hmdSI="

PUBLIC_KEY: bytes = b"""
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA8xbL4fLFhG4cj0hpoPVS
3nd9bNgIyvUO3I2yNzY/Rq29idRPOgDfbaGQZuCjiPNAidS9+7HwqLXUqV7+OMKW
ACQ+wHGgjpeFF9ZqG6WvHEWZgors8RAppL9kUEs9v5BoO0COD1Hm86TZWI8J46sL
Ebw2XAVKM6SKeTlITZEvINufS7biPBwO3dAIY7dB6x2upiBEQFdI2XZMV3GSLZ6W
EkE6ZWS3oMID84lFzUPIXxRxA59rlAKZ+fSCJxvg4HxeeR7nkTi0HCdF6h7VtPV5
RJGiJMXLK0kZ4Q2G7uA1ORJNa5E9n534nhFquHbjF6WJ07GTz8Y1tmYqdovQ1dtP
cwIDAQAB
-----END PUBLIC KEY-----
"""


if __name__ == '__main__':
    pass
