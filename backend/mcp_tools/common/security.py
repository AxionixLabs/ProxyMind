# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import (
    CallToolResult,
    TextContent
)
from backend.mcp_hub.hub_nexus import SecurityService
from backend.mcp_tools.common.schemas.schema_security import (
    OutputKeyArg,
    DigestKindArg,
    DigestInputArg,
    SecretValueArg,
    EncodingArg,
    DigestOutModeArg,
    JwtHsKindArg,
    JwtAsymmetricKindArg,
    JwtPayloadArg,
    JwtSecretArg,
    JwtTokenArg,
    JwtHeadersArg,
    JwtOptionsArg,
    ReturnPayloadArg,
    JwtCompleteArg,
    PrivateKeyArg,
    PublicKeyArg,
    CryptoKindArg,
    CryptoInputArg,
    SignatureArg,
    CiphertextArg,
    RsaOutModeArg,
    AesOutModeArg,
    BinaryFormatArg,
    PaddingArg,
    HashAlgorithmArg,
    MgfAlgorithmArg,
    RsaLabelArg,
    SaltLengthArg,
    AesKindArg,
    AesKeyArg,
    AesIvArg,
    AesNonceArg,
    AesAadArg,
    AesTagArg,
    AesModeArg,
    PaddingModeArg,
    SignTextKindArg,
    SignDataArg,
    SignItemsArg,
    PrefixArg,
    SuffixArg,
    PairSepArg,
    KvSepArg,
    SortKeysArg,
    IgnoreEmptyArg,
    IgnoreKeysArg,
    MultipartKindArg,
    MultipartFieldsArg,
    MultipartFilesArg,
    UseFilenameOnlyArg,
    IncludeContentTypeArg
)
from backend.utilities.runtime import AppContext


def _tool_result(agent_id: str, result: dict[str, typing.Any]) -> CallToolResult:
    """把 security 服务结果包装成统一的 MCP 返回结构。"""
    kind   = str(result["kind"])
    output = str(result["output"])
    text   = f"agent_id={agent_id} ok=True kind={kind} output={output}"

    structured: dict[str, typing.Any] | None = {
        "text" : text,
        "data" : result
    }

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured
    )


def bind(mcp: FastMCP, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "计算摘要或 HMAC。"
            " 该工具只负责确定性的加密摘要计算，不负责业务签名串拼装。"
            " `kind` 用来选择摘要或 HMAC 算法；当 `kind` 为 `hmac_*` 时必须同时提供 `secret`。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_digest(
        kind: DigestKindArg,
        output: OutputKeyArg,
        input_value: DigestInputArg,
        secret: SecretValueArg = None,
        encoding: EncodingArg = "utf-8",
        out_mode: DigestOutModeArg = "hex"
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "处理 HS256 JWT 的生成、无验签解析或验签解析。"
            " 该工具只覆盖对称密钥 JWT，不处理 RSA 或 EC 非对称签名。"
            " `kind` 决定当前模式；生成时需要 `payload` 和 `secret`，解析或验签时需要 `token`。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_jwt(
        kind: JwtHsKindArg,
        output: OutputKeyArg,
        payload: JwtPayloadArg = None,
        secret: JwtSecretArg = None,
        token: JwtTokenArg = None,
        headers: JwtHeadersArg = None,
        options: JwtOptionsArg = None,
        return_payload: ReturnPayloadArg = True,
        complete: JwtCompleteArg = False
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "处理 RS256 或 ES256 JWT 的生成与验签。"
            " 该工具面向非对称密钥 JWT，不处理 HS256 对称密钥场景。"
            " 私钥、公钥和 `kind` 必须对应，密钥格式不正确时会失败。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_jwt_rs(
        kind: JwtAsymmetricKindArg,
        output: OutputKeyArg,
        payload: JwtPayloadArg = None,
        token: JwtTokenArg = None,
        private_key: PrivateKeyArg = None,
        public_key: PublicKeyArg = None,
        headers: JwtHeadersArg = None,
        options: JwtOptionsArg = None,
        return_payload: ReturnPayloadArg = True
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "执行 RSA 签名、验签、公钥加密或私钥解密。"
            " 该工具只覆盖 RSA 相关能力，不处理 AES 或 JWT。"
            " `kind`、padding、摘要算法和输入格式必须与目标模式匹配。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_crypto(
        kind: CryptoKindArg,
        output: OutputKeyArg,
        input_value: CryptoInputArg = None,
        private_key: PrivateKeyArg = None,
        public_key: PublicKeyArg = None,
        signature: SignatureArg = None,
        ciphertext: CiphertextArg = None,
        encoding: EncodingArg = "utf-8",
        out_mode: RsaOutModeArg = "base64",
        signature_format: BinaryFormatArg = "base64",
        ciphertext_format: BinaryFormatArg = "base64",
        encrypt_padding: PaddingArg = "oaep",
        decrypt_padding: PaddingArg = "oaep",
        sign_padding: PaddingArg = "pkcs1v15",
        verify_padding: PaddingArg = "pkcs1v15",
        algorithm: HashAlgorithmArg = "sha256",
        mgf_algorithm: MgfAlgorithmArg = None,
        label: RsaLabelArg = None,
        salt_length: SaltLengthArg = "max"
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "执行 AES 加密或解密。"
            " 该工具支持 CBC、ECB 和 GCM，不同模式对 `iv`、`nonce`、`aad` 和 `tag` 的要求不同。"
            " GCM 模式下加密结果会同时包含密文和认证标签。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_aes(
        kind: AesKindArg,
        output: OutputKeyArg,
        input_value: CryptoInputArg = None,
        key: AesKeyArg = None,
        iv: AesIvArg = None,
        nonce: AesNonceArg = None,
        aad: AesAadArg = None,
        tag: AesTagArg = None,
        mode: AesModeArg = "cbc",
        key_format: BinaryFormatArg = "text",
        input_format: BinaryFormatArg = "text",
        iv_format: BinaryFormatArg = "text",
        nonce_format: BinaryFormatArg = "text",
        aad_format: BinaryFormatArg = "text",
        tag_format: BinaryFormatArg = "base64",
        out_mode: AesOutModeArg = "base64",
        encoding: EncodingArg = "utf-8",
        padding_mode: PaddingModeArg = "pkcs7"
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "生成业务签名前使用的确定性文本。"
            " 该工具只负责文本拼装，不负责摘要、加密或验签。"
            " `data`、`items`、排序规则和分隔符会直接影响最终签名串。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_sign_text(
        kind: SignTextKindArg,
        output: OutputKeyArg,
        data: SignDataArg = None,
        items: SignItemsArg = None,
        secret: SecretValueArg = None,
        prefix: PrefixArg = "",
        suffix: SuffixArg = "",
        pair_sep: PairSepArg = "&",
        kv_sep: KvSepArg = "=",
        sort_keys: SortKeysArg = True,
        ignore_empty: IgnoreEmptyArg = True,
        ignore_keys: IgnoreKeysArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "生成 multipart/form-data 场景的签名前文本。"
            " 该工具只负责签名文本拼装，不负责真实 multipart 编码或上传。"
            " 文件部分如何参与签名由 `kind`、`use_filename_only` 和 `include_content_type` 决定。"
        ),
        meta={"hidden": False, "domain": "common", "class": "security"}
    )
    def security_multipart_sign(
        kind: MultipartKindArg,
        output: OutputKeyArg,
        fields: MultipartFieldsArg = None,
        files: MultipartFilesArg = None,
        pair_sep: PairSepArg = "&",
        kv_sep: KvSepArg = "=",
        sort_keys: SortKeysArg = True,
        ignore_empty: IgnoreEmptyArg = True,
        ignore_keys: IgnoreKeysArg = None,
        use_filename_only: UseFilenameOnlyArg = True,
        include_content_type: IncludeContentTypeArg = False
    ) -> CallToolResult:
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
