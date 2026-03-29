# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import (
    CallToolResult, TextContent
)
from pydantic import Field
from backend.mcp_hub.hub_nexus import SecurityService
from backend.utilities.runtime import AppContext


OutputKeyArg = typing.Annotated[
    str,
    Field(description="结果在返回 `data` 中保存时使用的字段名。"),
]
DigestKindArg = typing.Annotated[
    str,
    Field(description="摘要或 HMAC 算法，如 md5、sha256、hmac_sha256。"),
]
DigestInputArg = typing.Annotated[
    typing.Any,
    Field(description="待计算摘要的输入值；文本会按 `encoding` 转字节。"),
]
SecretValueArg = typing.Annotated[
    typing.Any,
    Field(description="HMAC 密钥，或拼接签名文本时附加的密钥内容。"),
]
EncodingArg = typing.Annotated[
    str,
    Field(description="文本和字节之间转换时使用的字符编码。"),
]
DigestOutModeArg = typing.Annotated[
    str,
    Field(description="摘要结果输出格式，可选 `hex`、`base64` 或 `bytes`。"),
]
JwtHsKindArg = typing.Annotated[
    str,
    Field(description="HS256 JWT 模式，如 `jwt_hs256`、`jwt_decode_unverified` 或 `jwt_verify_hs256`。"),
]
JwtAsymmetricKindArg = typing.Annotated[
    str,
    Field(description="非对称 JWT 模式，如 `jwt_rs256`、`jwt_verify_rs256`、`jwt_es256` 或 `jwt_verify_es256`。"),
]
JwtPayloadArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 载荷。生成 token 时必填。"),
]
JwtSecretArg = typing.Annotated[
    str | None,
    Field(description="HS256 对称密钥。HS256 生成或验签时必填。"),
]
JwtTokenArg = typing.Annotated[
    str | None,
    Field(description="待解析或验签的 JWT 文本。"),
]
JwtHeadersArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 头部附加字段，如 `kid`。"),
]
JwtOptionsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 验签选项，会透传给底层 JWT 库。"),
]
ReturnPayloadArg = typing.Annotated[
    bool,
    Field(description="验签成功后是否返回解析出的 payload；为 false 时返回布尔结果。"),
]
JwtCompleteArg = typing.Annotated[
    bool,
    Field(description="无验签解析时是否返回 header、payload 和 signature 的完整结构。"),
]
PrivateKeyArg = typing.Annotated[
    str | None,
    Field(description="私钥 PEM 文本。生成 JWT、RSA 签名或 RSA 解密时通常需要。"),
]
PublicKeyArg = typing.Annotated[
    str | None,
    Field(description="公钥 PEM 文本。JWT 验签、RSA 验签或 RSA 加密时通常需要。"),
]
CryptoKindArg = typing.Annotated[
    str,
    Field(description="RSA 动作，如 `rsa_sign`、`rsa_verify`、`rsa_encrypt`、`rsa_decrypt` 或强语义别名。"),
]
CryptoInputArg = typing.Annotated[
    typing.Any,
    Field(description="RSA 或 AES 操作的主输入值，例如明文、待签名文本或待解密密文。"),
]
SignatureArg = typing.Annotated[
    typing.Any,
    Field(description="待验签的签名值；实际编码由 `signature_format` 决定。"),
]
CiphertextArg = typing.Annotated[
    typing.Any,
    Field(description="待解密的密文；实际编码由 `ciphertext_format` 或 `input_format` 决定。"),
]
RsaOutModeArg = typing.Annotated[
    str,
    Field(description="RSA 二进制结果的输出格式，可选 `base64`、`hex` 或 `bytes`。"),
]
AesOutModeArg = typing.Annotated[
    str,
    Field(description="AES 结果输出格式，可选 `base64`、`hex`、`bytes` 或 `text`。"),
]
BinaryFormatArg = typing.Annotated[
    str,
    Field(description="输入二进制值的编码格式，可选 `text`、`base64`、`hex` 或 `bytes`。"),
]
PaddingArg = typing.Annotated[
    str,
    Field(description="RSA padding 模式，如 `oaep`、`pkcs1v15` 或 `pss`。"),
]
HashAlgorithmArg = typing.Annotated[
    str,
    Field(description="摘要算法，如 `sha256`、`sha384` 或 `sha512`。"),
]
MgfAlgorithmArg = typing.Annotated[
    str | None,
    Field(description="OAEP 或 PSS 的 MGF1 摘要算法；为空时跟随 `algorithm`。"),
]
RsaLabelArg = typing.Annotated[
    str | None,
    Field(description="OAEP label 文本；为空时不传 label。"),
]
SaltLengthArg = typing.Annotated[
    str,
    Field(description="PSS salt 长度，可为 `max`、`digest`、`auto` 或非负整数文本。"),
]
AesKindArg = typing.Annotated[
    str,
    Field(description="AES 动作，支持 `aes_encrypt` 或 `aes_decrypt`。"),
]
AesKeyArg = typing.Annotated[
    typing.Any,
    Field(description="AES 密钥，解码后长度必须为 16、24 或 32 字节。"),
]
AesIvArg = typing.Annotated[
    typing.Any,
    Field(description="CBC 模式使用的初始化向量；解码后必须为 16 字节。"),
]
AesNonceArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 模式使用的 nonce。"),
]
AesAadArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 模式的附加认证数据；加解密两侧必须一致。"),
]
AesTagArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 解密时需要的认证标签。"),
]
AesModeArg = typing.Annotated[
    str,
    Field(description="AES 模式，可选 `cbc`、`ecb` 或 `gcm`。"),
]
PaddingModeArg = typing.Annotated[
    str,
    Field(description="分组填充模式，可选 `pkcs7` 或 `none`；GCM 下会被忽略。"),
]
SignTextKindArg = typing.Annotated[
    str,
    Field(description="签名文本拼装模式，如 `key_value_join`、`query_like`、`body_plus_secret` 或 `prefix_suffix`。"),
]
SignDataArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="键值对输入，适用于 `key_value_join` 或 `query_like`。"),
]
SignItemsArg = typing.Annotated[
    typing.Optional[list[typing.Any]],
    Field(description="顺序拼接的片段列表，适用于 `body_plus_secret` 或 `prefix_suffix`。"),
]
PrefixArg = typing.Annotated[
    str,
    Field(description="最终签名文本前缀。"),
]
SuffixArg = typing.Annotated[
    str,
    Field(description="最终签名文本后缀。"),
]
PairSepArg = typing.Annotated[
    str,
    Field(description="键值对之间的分隔符。"),
]
KvSepArg = typing.Annotated[
    str,
    Field(description="键和值之间的分隔符。"),
]
SortKeysArg = typing.Annotated[
    bool,
    Field(description="是否按键和值排序，以获得稳定输出。"),
]
IgnoreEmptyArg = typing.Annotated[
    bool,
    Field(description="是否忽略空值、空列表、空对象等空元素。"),
]
IgnoreKeysArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="需要从签名文本中排除的字段名列表。"),
]
MultipartKindArg = typing.Annotated[
    str,
    Field(description="multipart 签名文本模式，如 `multipart_field_text`、`multipart_file_text` 或 `multipart_all_text`。"),
]
MultipartFieldsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="multipart 普通字段字典。"),
]
MultipartFilesArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="multipart 文件项列表。每项通常包含 `field`、`filename`、`content_type` 和 `value`。"),
]
UseFilenameOnlyArg = typing.Annotated[
    bool,
    Field(description="文件部分参与签名时是否只使用文件名，而不使用文件内容。"),
]
IncludeContentTypeArg = typing.Annotated[
    bool,
    Field(description="文件部分参与签名时是否把 `content_type` 也拼进文本。"),
]


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
