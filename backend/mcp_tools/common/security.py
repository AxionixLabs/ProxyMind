#  ____                       _ _           ____                  _
# / ___|  ___  ___ _   _ _ __(_) |_ _   _  / ___|  ___ _ ____   _(_) ___ ___
# \___ \ / _ \/ __| | | | '__| | __| | | | \___ \ / _ \ '__\ \ / / |/ __/ _ \
#  ___) |  __/ (__| |_| | |  | | |_| |_| |  ___) |  __/ |   \ V /| | (_|  __/
# |____/ \___|\___|\__,_|_|  |_|\__|\__, | |____/ \___|_|    \_/ |_|\___\___|
#                                   |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import (
    CallToolResult, TextContent
)
from backend.mcp_hub.hub_nexus import SecurityService


def _tool_result(agent_id: str, result: dict[str, typing.Any]) -> CallToolResult:
    """把 security 服务结果包装成统一的 MCP 返回结构。"""
    kind   = str(result["kind"])
    output = str(result["output"])
    text   = f"agent_id={agent_id} ok=True kind={kind} output={output}"

    structured: typing.Any = {
        "text": text,
        "data": result
    }

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured
    )


def bind(mcp: FastMCP) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_digest(
        kind: str,
        output: str,
        input_value: typing.Any,
        secret: typing.Any = None,
        encoding: str = "utf-8",
        out_mode: str = "hex"
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_digest
        P:
          kind: str                  # md5 | sha1 | sha224 | sha256 | sha384 | sha512 | hmac_md5 | hmac_sha1 | hmac_sha224 | hmac_sha256 | hmac_sha384 | hmac_sha512
          output: str                # 输出变量名
          input_value: any           # 输入值
          secret: any=None           # HMAC 密钥；仅 hmac_* 需要
          encoding: str="utf-8"      # 文本转字节时使用的编码
          out_mode: str="hex"        # hex | base64 | bytes
        R: CTR
        N:
          - 计算摘要或 HMAC。
          - 该工具只负责确定性的加密摘要计算，不负责业务签名串拼装。
          - `kind`、`secret` 和 `out_mode` 共同决定最终输出形态。
        """
        return _tool_result(
            "security_digest",
            SecurityService.digest(
                kind=kind,
                output=output,
                input_value=input_value,
                secret=secret,
                encoding=encoding,
                out_mode=out_mode
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_jwt(
        kind: str,
        output: str,
        payload: typing.Optional[dict[str, typing.Any]] = None,
        secret: str | None = None,
        token: str | None = None,
        headers: typing.Optional[dict[str, typing.Any]] = None,
        options: typing.Optional[dict[str, typing.Any]] = None,
        return_payload: bool = True,
        complete: bool = False
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_jwt
        P:
          kind: str                        # jwt_hs256 | jwt_decode_unverified | jwt_verify_hs256
          output: str                      # 输出变量名
          payload: dict?=None              # jwt_hs256 的载荷
          secret: str?=None                # HS256 密钥
          token: str?=None                 # decode / verify 的输入 token
          headers: dict?=None              # jwt_hs256 headers
          options: dict?=None              # jwt_verify_hs256 options
          return_payload: bool=True        # verify 时返回 payload，否则返回 True
          complete: bool=False             # decode_unverified 是否返回完整结构
        R: CTR
        N:
          - 处理 HS256 JWT 的生成、无验签解析或验签解析。
          - 该工具只覆盖对称密钥 JWT，不处理 RSA 或 EC 非对称签名。
          - 生成、解析或验签由 `kind` 决定，输入字段需与对应模式匹配。
        """
        return _tool_result(
            "security_jwt",
            SecurityService.jwt_hs(
                kind=kind,
                output=output,
                payload=payload,
                secret=secret,
                token=token,
                headers=headers,
                options=options,
                return_payload=return_payload,
                complete=complete
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_jwt_rs(
        kind: str,
        output: str,
        payload: typing.Optional[dict[str, typing.Any]] = None,
        token: str | None = None,
        private_key: str | None = None,
        public_key: str | None = None,
        headers: typing.Optional[dict[str, typing.Any]] = None,
        options: typing.Optional[dict[str, typing.Any]] = None,
        return_payload: bool = True
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_jwt_rs
        P:
          kind: str                        # jwt_rs256 | jwt_verify_rs256 | jwt_es256 | jwt_verify_es256
          output: str                      # 输出变量名
          payload: dict?=None              # jwt_rs256 / jwt_es256 载荷
          token: str?=None                 # jwt_verify_rs256 / jwt_verify_es256 输入 token
          private_key: str?=None           # jwt_rs256 / jwt_es256 私钥
          public_key: str?=None            # jwt_verify_rs256 / jwt_verify_es256 公钥
          headers: dict?=None              # 生成 token 时附带的 headers
          options: dict?=None              # verify options
          return_payload: bool=True        # verify 时返回 payload，否则返回 True
        R: CTR
        N:
          - 处理 RS256 或 ES256 JWT 的生成与验签。
          - 该工具面向非对称密钥 JWT，不处理 HS256 对称密钥场景。
          - 私钥、公钥和 `kind` 必须对应；密钥格式不正确时会失败。
        """
        return _tool_result(
            "security_jwt_rs",
            SecurityService.jwt_asymmetric(
                kind=kind,
                output=output,
                payload=payload,
                token=token,
                private_key=private_key,
                public_key=public_key,
                headers=headers,
                options=options,
                return_payload=return_payload
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_crypto(
        kind: str,
        output: str,
        input_value: typing.Any = None,
        private_key: str | None = None,
        public_key: str | None = None,
        signature: typing.Any = None,
        ciphertext: typing.Any = None,
        encoding: str = "utf-8",
        out_mode: str = "base64",
        signature_format: str = "base64",
        ciphertext_format: str = "base64",
        encrypt_padding: str = "oaep",
        decrypt_padding: str = "oaep",
        sign_padding: str = "pkcs1v15",
        verify_padding: str = "pkcs1v15",
        algorithm: str = "sha256",
        mgf_algorithm: str | None = None,
        label: str | None = None,
        salt_length: str = "max"
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_crypto
        P:
          kind: str                        # rsa_sign | rsa_verify | rsa_encrypt | rsa_decrypt | rsa_encrypt_oaep_sha256 | rsa_decrypt_oaep_sha256 | rsa_sign_pss_sha256 | rsa_verify_pss_sha256
          output: str                      # 输出变量名
          input_value: any=None            # sign / encrypt / verify 的原文输入
          private_key: str?=None           # sign / decrypt 私钥
          public_key: str?=None            # verify / encrypt 公钥
          signature: any=None              # verify 输入签名
          ciphertext: any=None             # decrypt 输入密文
          encoding: str="utf-8"            # 文本编码
          out_mode: str="base64"           # sign / encrypt 输出格式：base64 | hex | bytes
          signature_format: str="base64"   # verify 输入签名格式：base64 | hex | bytes
          ciphertext_format: str="base64"  # decrypt 输入密文格式：base64 | hex | bytes
          encrypt_padding: str="oaep"      # oaep | pkcs1v15
          decrypt_padding: str="oaep"      # oaep | pkcs1v15
          sign_padding: str="pkcs1v15"     # pkcs1v15 | pss
          verify_padding: str="pkcs1v15"   # pkcs1v15 | pss
          algorithm: str="sha256"          # sha1 | sha224 | sha256 | sha384 | sha512
          mgf_algorithm: str?=None         # OAEP / PSS 的 MGF1 摘要算法
          label: str?=None                 # OAEP label
          salt_length: str="max"           # PSS salt 长度：max | digest | auto | 整数文本
        R: CTR
        N:
          - 执行 RSA 签名、验签、公钥加密或私钥解密。
          - 该工具只覆盖 RSA 相关能力，不处理 AES 或 JWT。
          - `kind` 决定具体动作；padding、摘要算法和输入格式需要与目标模式匹配。
        """
        return _tool_result(
            "security_crypto",
            SecurityService.crypto(
                kind=kind,
                output=output,
                input_value=input_value,
                private_key=private_key,
                public_key=public_key,
                signature=signature,
                ciphertext=ciphertext,
                encoding=encoding,
                out_mode=out_mode,
                signature_format=signature_format,
                ciphertext_format=ciphertext_format,
                encrypt_padding=encrypt_padding,
                decrypt_padding=decrypt_padding,
                sign_padding=sign_padding,
                verify_padding=verify_padding,
                algorithm=algorithm,
                mgf_algorithm=mgf_algorithm,
                label=label,
                salt_length=salt_length
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_aes(
        kind: str,
        output: str,
        input_value: typing.Any = None,
        key: typing.Any = None,
        iv: typing.Any = None,
        nonce: typing.Any = None,
        aad: typing.Any = None,
        tag: typing.Any = None,
        mode: str = "cbc",
        key_format: str = "text",
        input_format: str = "text",
        iv_format: str = "text",
        nonce_format: str = "text",
        aad_format: str = "text",
        tag_format: str = "base64",
        out_mode: str = "base64",
        encoding: str = "utf-8",
        padding_mode: str = "pkcs7"
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_aes
        P:
          kind: str                        # aes_encrypt | aes_decrypt
          output: str                      # 输出变量名
          input_value: any=None            # 输入明文或密文
          key: any=None                    # AES key
          iv: any=None                     # CBC 用 IV
          nonce: any=None                  # GCM 用 nonce
          aad: any=None                    # GCM 附加认证数据
          tag: any=None                    # GCM 解密 tag
          mode: str="cbc"                  # cbc | ecb | gcm
          key_format: str="text"           # text | base64 | hex | bytes
          input_format: str="text"         # text | base64 | hex | bytes
          iv_format: str="text"            # text | base64 | hex | bytes
          nonce_format: str="text"         # text | base64 | hex | bytes
          aad_format: str="text"           # text | base64 | hex | bytes
          tag_format: str="base64"         # text | base64 | hex | bytes
          out_mode: str="base64"           # text | base64 | hex | bytes
          encoding: str="utf-8"            # 文本编码
          padding_mode: str="pkcs7"        # cbc/ecb 可用：pkcs7 | none
        R: CTR
        N:
          - 执行 AES 加密或解密。
          - 支持 CBC、ECB 和 GCM；不同模式对 `iv`、`nonce`、`aad`、`tag` 的要求不同。
          - GCM 模式下加密结果会同时包含密文和认证标签。
        """
        return _tool_result(
            "security_aes",
            SecurityService.aes(
                kind=kind,
                output=output,
                input_value=input_value,
                key=key,
                iv=iv,
                nonce=nonce,
                aad=aad,
                tag=tag,
                mode=mode,
                key_format=key_format,
                input_format=input_format,
                iv_format=iv_format,
                nonce_format=nonce_format,
                aad_format=aad_format,
                tag_format=tag_format,
                out_mode=out_mode,
                encoding=encoding,
                padding_mode=padding_mode
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_sign_text(
        kind: str,
        output: str,
        data: typing.Optional[dict[str, typing.Any]] = None,
        items: typing.Optional[list[typing.Any]] = None,
        secret: typing.Any = None,
        prefix: str = "",
        suffix: str = "",
        pair_sep: str = "&",
        kv_sep: str = "=",
        sort_keys: bool = True,
        ignore_empty: bool = True,
        ignore_keys: typing.Optional[list[str]] = None
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_sign_text
        P:
          kind: str                        # key_value_join | query_like | body_plus_secret | prefix_suffix
          output: str                      # 输出变量名
          data: dict?=None                 # key_value_join / query_like 输入
          items: list?=None                # body_plus_secret / prefix_suffix 输入
          secret: any=None                 # body_plus_secret 密钥
          prefix: str=""                   # 前缀
          suffix: str=""                   # 后缀
          pair_sep: str="&"                # 键值对分隔符
          kv_sep: str="="                  # 键值分隔符
          sort_keys: bool=True             # 是否按 key 排序
          ignore_empty: bool=True          # 是否忽略空值
          ignore_keys: list[str]?=None     # 忽略字段名
        R: CTR
        N:
          - 生成业务签名前使用的确定性文本。
          - 该工具只负责文本拼装，不负责摘要、加密或验签。
          - `data`、`items`、排序规则和分隔符会直接影响最终签名串。
        """
        return _tool_result(
            "security_sign_text",
            SecurityService.sign_text(
                kind=kind,
                output=output,
                data=data,
                items=items,
                secret=secret,
                prefix=prefix,
                suffix=suffix,
                pair_sep=pair_sep,
                kv_sep=kv_sep,
                sort_keys=sort_keys,
                ignore_empty=ignore_empty,
                ignore_keys=ignore_keys
            )
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "security"})
    def security_multipart_sign(
        kind: str,
        output: str,
        fields: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None,
        pair_sep: str = "&",
        kv_sep: str = "=",
        sort_keys: bool = True,
        ignore_empty: bool = True,
        ignore_keys: typing.Optional[list[str]] = None,
        use_filename_only: bool = True,
        include_content_type: bool = False
    ) -> CallToolResult:
        """
        D: common
        C: security
        A: security_multipart_sign
        P:
          kind: str                        # multipart_field_text | multipart_file_text | multipart_all_text
          output: str                      # 输出变量名
          fields: dict?=None               # 表单字段
          files: list[dict]?=None          # 文件列表：[{field, filename, content_type, value}]
          pair_sep: str="&"                # 键值对分隔符
          kv_sep: str="="                  # 键值分隔符
          sort_keys: bool=True             # 是否排序
          ignore_empty: bool=True          # 是否忽略空值
          ignore_keys: list[str]?=None     # 忽略字段名
          use_filename_only: bool=True     # 文件签名文本是否仅取 filename
          include_content_type: bool=False # 文件签名文本是否包含 content_type
        R: CTR
        N:
          - 生成 multipart/form-data 场景的签名前文本。
          - 该工具只负责签名文本拼装，不负责真实 multipart 编码或上传。
          - 文件部分如何参与签名，由 `kind`、`use_filename_only` 和 `include_content_type` 决定。
        """
        return _tool_result(
            "security_multipart_sign",
            SecurityService.multipart_sign(
                kind=kind,
                output=output,
                fields=fields,
                files=files,
                pair_sep=pair_sep,
                kv_sep=kv_sep,
                sort_keys=sort_keys,
                ignore_empty=ignore_empty,
                ignore_keys=ignore_keys,
                use_filename_only=use_filename_only,
                include_content_type=include_content_type
            )
        )


if __name__ == '__main__':
    pass
