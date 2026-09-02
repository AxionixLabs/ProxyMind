# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from metadata import const

CHARSET            = const.CHARSET
CLIENT_NAME        = const.APP_NAME
CLIENT_DESCRIPTION = const.APP_DESC
CLIENT_VERSION     = const.APP_VERSION
CLIENT_PUBLISHER   = const.PUBLISHER

BASE_URL = r"http://127.0.0.1:3333"
MCP_ED   = r"/helix/mcp"

MASTER   = r"7zUyfFya8Av0_ixhxKgLeGfVkKF0xy5qQw9pGnEobEZx6kgjKmrUVHiUvdlibNKwybf_H1vRt7_-2PfMLmtACA%"
ISSUER   = r"https://auth.helix.local/issuer"
AUDIENCE = r"helix-mcp-api"

AGENT_CLIENT_SECRET = r"177P81LAw5fdeUp2IRX9q-i6hW9gCPTiBHKzkpSC9tE"
AGENT_ADMIN_SECRET  = r"U2d73xNcBFU8Gewr9DKk_-8-048tfosxfFvqiu0v_Wo"
SHARED_SECRET       = r"xosspWbJNo9hUjR4OceTuSLshorCn0IXucTKO0hmdSI="

DOMAIN = r"https://api.appserverx.com"

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
BOOTSTRAP_URL     = f"{DOMAIN}/bootstrap"

TEMPLATE_META_URL  = r""
BUSINESS_CASE_URL  = r""
SPEECH_META_URL    = r""
SPEECH_VOICE_URL   = r""
GLOBAL_CF_URL      = f"{DOMAIN}/global-configuration"
PREDICT_URL        = r""
TOOLKIT_META_URL   = r""
MODEL_META_URL     = r""
X_TEMPLATE_VERSION = r""

KEEPALIVE_SEC         = 300.0
KEEPALIVE_TIMEOUT_SEC = 3.0

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
