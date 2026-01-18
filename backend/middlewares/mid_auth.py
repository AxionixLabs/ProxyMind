#     _         _   _       __  __ _     _     _ _
#    / \  _   _| |_| |__   |  \/  (_) __| | __| | | _____      ____ _ _ __ ___
#   / _ \| | | | __| '_ \  | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \
#  / ___ \ |_| | |_| | | | | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/
# /_/   \_\__,_|\__|_| |_| |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import jwt
import hmac
import time
import base64
import typing
import hashlib
from jwt.exceptions import InvalidTokenError
from mcp.server.auth.provider import (
    TokenVerifier, AccessToken
)
from backend.utilities import const


def derive_hs256_secret(*, step_sec: int = 300, ts: int | None = None) -> str:
    """
    step_sec=300 -> 5分钟轮换一次
    返回 base64url 的派生 secret（字符串）
    """
    if ts is None: ts = int(time.time())

    bucket = ts // step_sec

    msg = str(bucket).encode(const.CHARSET)
    key = const.MASTER.encode(const.CHARSET)

    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode(const.CHARSET).rstrip("=")


class HelixTokenVerifier(TokenVerifier):
    """HelixTokenVerifier class."""

    async def verify_token(self, token: str) -> typing.Optional[AccessToken]:
        """
        允许三个时间窗口：当前/上一窗口/下一窗口（各 5 分钟一档）
        目的：容忍客户端与服务端的轻微时钟偏差，避免刚切桶时校验失败
        """
        now, step_sec = int(time.time()), 300

        for ts in (now := int(time.time()), now - step_sec, now + step_sec):
            # 基于 master secret + 时间桶(bucket)派生出本窗口的临时 HS256 secret
            secret = derive_hs256_secret(ts=ts)

            try:
                # 校验 JWT 签名(HS256) + issuer/audience + exp/iss/aud 必须存在
                claims = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256"],
                    issuer=const.ISSUER,
                    audience=const.AUDIENCE,
                    options={"require": ["exp", "iss", "aud"]},
                )

                """
                将 token 映射为 MCP 的 AccessToken
                    - token: 原始 Bearer token（用于审计/透传）
                    - client_id: 你的调用方标识（自定义）
                    - scopes: 用于 required_scopes 校验
                """
                return AccessToken(
                    token=token,
                    client_id="helix",
                    scopes=claims.get("scope", "").split()
                )

            except InvalidTokenError:
                continue  # 当前窗口校验失败：继续尝试其它窗口（上一/下一）

        return None  # 三个窗口都无法校验通过：判定 token 无效


if __name__ == '__main__':
    pass
