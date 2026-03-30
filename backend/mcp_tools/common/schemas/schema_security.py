# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


OutputKeyArg = typing.Annotated[
    str,
    Field(description="结果在返回 `data` 中保存时使用的字段名。")
]
DigestKindArg = typing.Annotated[
    str,
    Field(description="摘要或 HMAC 算法，如 md5、sha256、hmac_sha256。")
]
DigestInputArg = typing.Annotated[
    typing.Any,
    Field(description="待计算摘要的输入值；文本会按 `encoding` 转字节。")
]
SecretValueArg = typing.Annotated[
    typing.Any,
    Field(description="HMAC 密钥，或拼接签名文本时附加的密钥内容。")
]
EncodingArg = typing.Annotated[
    str,
    Field(description="文本和字节之间转换时使用的字符编码。")
]
DigestOutModeArg = typing.Annotated[
    str,
    Field(description="摘要结果输出格式，可选 `hex`、`base64` 或 `bytes`。")
]
JwtHsKindArg = typing.Annotated[
    str,
    Field(description="HS256 JWT 模式，如 `jwt_hs256`、`jwt_decode_unverified` 或 `jwt_verify_hs256`。")
]
JwtAsymmetricKindArg = typing.Annotated[
    str,
    Field(description="非对称 JWT 模式，如 `jwt_rs256`、`jwt_verify_rs256`、`jwt_es256` 或 `jwt_verify_es256`。")
]
JwtPayloadArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 载荷。生成 token 时必填。")
]
JwtSecretArg = typing.Annotated[
    str | None,
    Field(description="HS256 对称密钥。HS256 生成或验签时必填。")
]
JwtTokenArg = typing.Annotated[
    str | None,
    Field(description="待解析或验签的 JWT 文本。")
]
JwtHeadersArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 头部附加字段，如 `kid`。")
]
JwtOptionsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="JWT 验签选项，会透传给底层 JWT 库。")
]
ReturnPayloadArg = typing.Annotated[
    bool,
    Field(description="验签成功后是否返回解析出的 payload；为 false 时返回布尔结果。")
]
JwtCompleteArg = typing.Annotated[
    bool,
    Field(description="无验签解析时是否返回 header、payload 和 signature 的完整结构。")
]
PrivateKeyArg = typing.Annotated[
    str | None,
    Field(description="私钥 PEM 文本。生成 JWT、RSA 签名或 RSA 解密时通常需要。")
]
PublicKeyArg = typing.Annotated[
    str | None,
    Field(description="公钥 PEM 文本。JWT 验签、RSA 验签或 RSA 加密时通常需要。")
]
CryptoKindArg = typing.Annotated[
    str,
    Field(description="RSA 动作，如 `rsa_sign`、`rsa_verify`、`rsa_encrypt`、`rsa_decrypt` 或强语义别名。")
]
CryptoInputArg = typing.Annotated[
    typing.Any,
    Field(description="RSA 或 AES 操作的主输入值，例如明文、待签名文本或待解密密文。")
]
SignatureArg = typing.Annotated[
    typing.Any,
    Field(description="待验签的签名值；实际编码由 `signature_format` 决定。")
]
CiphertextArg = typing.Annotated[
    typing.Any,
    Field(description="待解密的密文；实际编码由 `ciphertext_format` 或 `input_format` 决定。")
]
RsaOutModeArg = typing.Annotated[
    str,
    Field(description="RSA 二进制结果的输出格式，可选 `base64`、`hex` 或 `bytes`。")
]
AesOutModeArg = typing.Annotated[
    str,
    Field(description="AES 结果输出格式，可选 `base64`、`hex`、`bytes` 或 `text`。")
]
BinaryFormatArg = typing.Annotated[
    str,
    Field(description="输入二进制值的编码格式，可选 `text`、`base64`、`hex` 或 `bytes`。")
]
PaddingArg = typing.Annotated[
    str,
    Field(description="RSA padding 模式，如 `oaep`、`pkcs1v15` 或 `pss`。")
]
HashAlgorithmArg = typing.Annotated[
    str,
    Field(description="摘要算法，如 `sha256`、`sha384` 或 `sha512`。")
]
MgfAlgorithmArg = typing.Annotated[
    str | None,
    Field(description="OAEP 或 PSS 的 MGF1 摘要算法；为空时跟随 `algorithm`。")
]
RsaLabelArg = typing.Annotated[
    str | None,
    Field(description="OAEP label 文本；为空时不传 label。")
]
SaltLengthArg = typing.Annotated[
    str,
    Field(description="PSS salt 长度，可为 `max`、`digest`、`auto` 或非负整数文本。")
]
AesKindArg = typing.Annotated[
    str,
    Field(description="AES 动作，支持 `aes_encrypt` 或 `aes_decrypt`。")
]
AesKeyArg = typing.Annotated[
    typing.Any,
    Field(description="AES 密钥，解码后长度必须为 16、24 或 32 字节。")
]
AesIvArg = typing.Annotated[
    typing.Any,
    Field(description="CBC 模式使用的初始化向量；解码后必须为 16 字节。")
]
AesNonceArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 模式使用的 nonce。")
]
AesAadArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 模式的附加认证数据；加解密两侧必须一致。")
]
AesTagArg = typing.Annotated[
    typing.Any,
    Field(description="GCM 解密时需要的认证标签。")
]
AesModeArg = typing.Annotated[
    str,
    Field(description="AES 模式，可选 `cbc`、`ecb` 或 `gcm`。")
]
PaddingModeArg = typing.Annotated[
    str,
    Field(description="分组填充模式，可选 `pkcs7` 或 `none`；GCM 下会被忽略。")
]
SignTextKindArg = typing.Annotated[
    str,
    Field(description="签名文本拼装模式，如 `key_value_join`、`query_like`、`body_plus_secret` 或 `prefix_suffix`。")
]
SignDataArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="键值对输入，适用于 `key_value_join` 或 `query_like`。")
]
SignItemsArg = typing.Annotated[
    typing.Optional[list[typing.Any]],
    Field(description="顺序拼接的片段列表，适用于 `body_plus_secret` 或 `prefix_suffix`。")
]
PrefixArg = typing.Annotated[
    str,
    Field(description="最终签名文本前缀。")
]
SuffixArg = typing.Annotated[
    str,
    Field(description="最终签名文本后缀。")
]
PairSepArg = typing.Annotated[
    str,
    Field(description="键值对之间的分隔符。"),
]
KvSepArg = typing.Annotated[
    str,
    Field(description="键和值之间的分隔符。")
]
SortKeysArg = typing.Annotated[
    bool,
    Field(description="是否按键和值排序，以获得稳定输出。")
]
IgnoreEmptyArg = typing.Annotated[
    bool,
    Field(description="是否忽略空值、空列表、空对象等空元素。")
]
IgnoreKeysArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="需要从签名文本中排除的字段名列表。")
]
MultipartKindArg = typing.Annotated[
    str,
    Field(description="multipart 签名文本模式，如 `multipart_field_text`、`multipart_file_text` 或 `multipart_all_text`。")
]
MultipartFieldsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="multipart 普通字段字典。")
]
MultipartFilesArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="multipart 文件项列表。每项通常包含 `field`、`filename`、`content_type` 和 `value`。")
]
UseFilenameOnlyArg = typing.Annotated[
    bool,
    Field(description="文件部分参与签名时是否只使用文件名，而不使用文件内容。")
]
IncludeContentTypeArg = typing.Annotated[
    bool,
    Field(description="文件部分参与签名时是否把 `content_type` 也拼进文本。")
]


if __name__ == '__main__':
    pass
