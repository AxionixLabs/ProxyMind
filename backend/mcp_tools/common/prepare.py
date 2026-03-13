#  ____
# |  _ \ _ __ ___ _ __   __ _ _ __ ___
# | |_) | '__/ _ \ '_ \ / _` | '__/ _ \
# |  __/| | |  __/ |_) | (_| | | |  __/
# |_|   |_|  \___| .__/ \__,_|_|  \___|
#                |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import jwt
import gzip
import hmac
import json
import time
import uuid
import zlib
import base64
import string
import typing
import secrets
import hashlib
from datetime import (
    datetime, timedelta, timezone
)
from urllib.parse import (
    quote, unquote, urlencode, parse_qs
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import (
    hashes, serialization
)
from cryptography.hazmat.primitives.asymmetric import (
    padding, rsa
)
from cryptography.hazmat.primitives.ciphers import (
    Cipher, algorithms, modes
)
from cryptography.hazmat.primitives import padding as sym_padding
from mcp.server import FastMCP
from mcp.types import (
    CallToolResult, TextContent
)

"""
1. 标识生成
2. 编码与序列化
3. 摘要与消息认证
4. JWT 令牌处理
5. 非对称密码能力
6. 对称加解密能力
7. URL 与表单处理
8. 值处理与上下文取值
9. 文本拼装
10. 签名串构造
11. 时间处理
12. 压缩与解压
13. 结构归一化处理
"""


def bind(mcp: FastMCP) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_ident(
        kind: str,
        output: str,
        length: int = 16,
        min_value: int = 0,
        max_value: int = 999999,
        alphabet: str | None = None
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_ident
        P:
          kind: str                 # uuid4 | timestamp_ms | timestamp_s | nonce | random_int | random_text
          output: str               # 输出变量名
          length: int=16            # nonce / random_text 长度
          min_value: int=0          # random_int 下界
          max_value: int=999999     # random_int 上界
          alphabet: str?=None       # nonce / random_text 字符集；未填时用字母数字
        R: CTR
        N:
          - 用于生成轻量标识类变量
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        if tp == "uuid4":
            value: typing.Any = str(uuid.uuid4())

        elif tp == "timestamp_ms":
            value = int(time.time() * 1000)

        elif tp == "timestamp_s":
            value = int(time.time())

        elif tp == "nonce":
            n = max(1, int(length))
            alphabet_v: str = alphabet if isinstance(
                alphabet, str
            ) and alphabet else (string.ascii_letters + string.digits)
            value = "".join(secrets.choice(alphabet_v) for _ in range(n))

        elif tp == "random_text":
            n = max(1, int(length))
            alphabet_v: str = alphabet if isinstance(
                alphabet, str
            ) and alphabet else (string.ascii_letters + string.digits)
            value = "".join(secrets.choice(alphabet_v) for _ in range(n))

        elif tp == "random_int":
            lo = int(min_value)
            hi = int(max_value)
            if lo > hi:
                raise ValueError("min_value > max_value")
            value = secrets.randbelow(hi - lo + 1) + lo

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_ident ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : value,
                "data"   : {out: value}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_codec(
        kind: str,
        output: str,
        input_value: typing.Any,
        encoding: str = "utf-8",
        as_text: bool = True,
        strip_padding: bool = False,
        ensure_ascii: bool = False,
        sort_keys: bool = False,
        separators: typing.Optional[list[str]] = None
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_codec
        P:
          kind: str                         # base64_encode | base64_decode | base64url_encode | base64url_decode | hex_encode | hex_decode | json_dumps | json_loads
          output: str                       # 输出变量名
          input_value: any                  # 输入值
          encoding: str="utf-8"             # 文本编码
          as_text: bool=True                # decode 后是否转文本
          strip_padding: bool=False         # base64url_encode 是否去掉 =
          ensure_ascii: bool=False          # json_dumps 参数
          sort_keys: bool=False             # json_dumps 参数
        R: CTR
        N:
          - 用于编码、解码、序列化、反序列化
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _to_text(v: typing.Any) -> str:
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if isinstance(v, bytes):
                return v.decode(encoding)
            return str(v)

        def _to_bytes(v: typing.Any) -> bytes:
            if v is None:
                return b""
            if isinstance(v, bytes):
                return v
            if isinstance(v, bytearray):
                return bytes(v)
            if isinstance(v, str):
                return v.encode(encoding)
            return str(v).encode(encoding)

        if tp == "base64_encode":
            result: typing.Any = base64.b64encode(_to_bytes(input_value)).decode("ascii")

        elif tp == "base64_decode":
            raw = _to_text(input_value).strip()
            try:
                decoded = base64.b64decode(raw, validate=True)
            except Exception as exc:
                raise ValueError("invalid base64 input") from exc
            result = decoded.decode(encoding) if as_text else decoded

        elif tp == "base64url_encode":
            raw = base64.urlsafe_b64encode(_to_bytes(input_value)).decode("ascii")
            result = raw.rstrip("=") if strip_padding else raw

        elif tp == "base64url_decode":
            raw = _to_text(input_value).strip()

            if not raw:
                decoded = b""
            else:
                normalized = raw.replace("-", "+").replace("_", "/")
                pad = (-len(normalized)) % 4
                if pad:
                    normalized += "=" * pad
                try:
                    decoded = base64.b64decode(normalized, validate=True)
                except Exception as exc:
                    raise ValueError("invalid base64url input") from exc

            result = decoded.decode(encoding) if as_text else decoded

        elif tp == "hex_encode":
            result = _to_bytes(input_value).hex()

        elif tp == "hex_decode":
            decoded = bytes.fromhex(_to_text(input_value).strip())
            result = decoded.decode(encoding) if as_text else decoded

        elif tp == "json_dumps":
            seps: tuple[str, str] | None = None
            if separators is not None:
                if not isinstance(separators, list) or len(separators) != 2:
                    raise ValueError("separators must be 2-item list")
                seps = (str(separators[0]), str(separators[1]))

            result = json.dumps(
                input_value,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
                separators=seps if seps is not None else (",", ":")
            )

        elif tp == "json_loads":
            result = json.loads(_to_text(input_value))

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_codec ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_digest(
        kind: str,
        output: str,
        input_value: typing.Any,
        secret: typing.Any = None,
        encoding: str = "utf-8",
        out_mode: str = "hex"
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_digest
        P:
          kind: str                         # md5 | sha1 | sha256 | sha512 | hmac_md5 | hmac_sha1 | hmac_sha256 | hmac_sha512
          output: str                       # 输出变量名
          input_value: any                  # 输入值
          secret: any=None                  # hmac 密钥
          encoding: str="utf-8"             # 文本编码
          out_mode: str="hex"               # hex | base64 | bytes
        R: CTR
        N:
          - 用于摘要与 HMAC 生成
          - hmac_* 必须传 secret
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        mode = str(out_mode or "hex").strip().lower()
        if not out:
            raise ValueError("output is required")

        def _to_bytes(v: typing.Any) -> bytes:
            if v is None:
                return b""
            if isinstance(v, bytes):
                return v
            if isinstance(v, bytearray):
                return bytes(v)
            if isinstance(v, str):
                return v.encode(encoding)
            return str(v).encode(encoding)

        def _format_digest(d: bytes) -> typing.Any:
            if mode == "hex":
                return d.hex()
            if mode == "base64":
                return base64.b64encode(d).decode("ascii")
            if mode == "bytes":
                return d
            raise ValueError(f"unsupported out_mode: {out_mode}")

        data_in = _to_bytes(input_value)

        if tp in {"md5", "sha1", "sha256", "sha512"}:
            h = hashlib.new(tp)
            h.update(data_in)
            result: typing.Any = _format_digest(h.digest())

        elif tp in {"hmac_md5", "hmac_sha1", "hmac_sha256", "hmac_sha512"}:
            if secret is None:
                raise ValueError("secret is required")
            algo = tp.replace("hmac_", "")
            key = _to_bytes(secret)
            digest = hmac.new(key, data_in, getattr(hashlib, algo)).digest()
            result = _format_digest(digest)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_digest ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_jwt(
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
        C: prepare
        A: prepare_jwt
        P:
          kind: str                         # jwt_hs256 | jwt_decode_unverified | jwt_verify_hs256
          output: str                       # 输出变量名
          payload: dict?=None               # jwt_hs256 载荷
          secret: str?=None                 # hs256 密钥
          token: str?=None                  # decode / verify 输入 token
          headers: dict?=None               # jwt_hs256 headers
          options: dict?=None               # jwt_verify_hs256 options
          return_payload: bool=True         # verify 时返回 payload，否则返回 True
          complete: bool=False              # decode_unverified 是否返回完整结构
        R: CTR
        N:
          - 用于 JWT HS256 的生成、无验签解析、验签解析
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        if tp == "jwt_hs256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not secret:
                raise ValueError("secret is required")

            result: typing.Any = jwt.encode(
                payload,
                secret,
                algorithm="HS256",
                headers=headers
            )

        elif tp == "jwt_decode_unverified":
            if not token:
                raise ValueError("token is required")

            if complete:
                result = jwt.decode_complete(
                    token,
                    options={"verify_signature": False}
                )
            else:
                result = jwt.decode(
                    token,
                    options={"verify_signature": False}
                )

        elif tp == "jwt_verify_hs256":
            if not token:
                raise ValueError("token is required")
            if not secret:
                raise ValueError("secret is required")

            decoded = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options=options or {}
            )
            result = decoded if return_payload else True

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_jwt ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_jwt_rs(
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
        C: prepare
        A: prepare_jwt_rs
        P:
          kind: str                         # jwt_rs256 | jwt_verify_rs256
          output: str                       # 输出变量名
          payload: dict?=None               # jwt_rs256 载荷
          token: str?=None                  # jwt_verify_rs256 输入 token
          private_key: str?=None            # jwt_rs256 私钥；支持完整 PEM 或裸 key 字符串
          public_key: str?=None             # jwt_verify_rs256 公钥；支持完整 PEM 或裸 key 字符串
          headers: dict?=None               # jwt_rs256 headers
          options: dict?=None               # jwt_verify_rs256 options
          return_payload: bool=True         # verify 时返回 payload，否则返回 True
        R: CTR
        N:
          - 用于 JWT RS256 的生成与验签解析
          - 私钥兼容 PKCS8 / PKCS1；公钥兼容 SPKI / PKCS1
          - key 支持完整 PEM，也支持裸字符串，内部会自动补 BEGIN/END
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _normalize_pem_key(key_value: str, pem_types: list[str]) -> str:
            normalized = str(key_value or "").strip()
            if not normalized:
                raise ValueError("key is required")

            if "-----BEGIN " in normalized and "-----END " in normalized:
                return normalized

            body_only = "".join(normalized.split())
            if not body_only:
                raise ValueError("key is empty")

            head_type = pem_types[0]
            chunks = [body_only[i:i + 64] for i in range(0, len(body_only), 64)]
            return (
                f"-----BEGIN {head_type}-----\n"
                + "\n".join(chunks)
                + f"\n-----END {head_type}-----\n"
            )

        def _normalize_private_key_for_jwt(key_value: str) -> str:
            last_error: Exception | None = None
            for pem_text in (
                    _normalize_pem_key(key_value, ["PRIVATE KEY"]),
                    _normalize_pem_key(key_value, ["RSA PRIVATE KEY"])
            ):
                try:
                    serialization.load_pem_private_key(
                        pem_text.encode("utf-8"),
                        password=None
                    )
                    return pem_text
                except Exception as exc:
                    last_error = exc

            raise ValueError(f"private_key load failed: {last_error}")

        def _normalize_public_key_for_jwt(key_value: str) -> str:
            last_error: Exception | None = None
            for pem_text in (
                    _normalize_pem_key(key_value, ["PUBLIC KEY"]),
                    _normalize_pem_key(key_value, ["RSA PUBLIC KEY"])
            ):
                try:
                    serialization.load_pem_public_key(
                        pem_text.encode("utf-8")
                    )
                    return pem_text
                except Exception as exc:
                    last_error = exc

            raise ValueError(f"public_key load failed: {last_error}")

        if tp == "jwt_rs256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not private_key:
                raise ValueError("private_key is required")

            normalized_private_key = _normalize_private_key_for_jwt(private_key)

            result: typing.Any = jwt.encode(
                payload,
                normalized_private_key,
                algorithm="RS256",
                headers=headers
            )

        elif tp == "jwt_verify_rs256":
            if not token:
                raise ValueError("token is required")
            if not public_key:
                raise ValueError("public_key is required")

            normalized_public_key = _normalize_public_key_for_jwt(public_key)

            decoded = jwt.decode(
                token,
                normalized_public_key,
                algorithms=["RS256"],
                options=options or {}
            )
            result = decoded if return_payload else True

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_jwt_rs ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind": tp,
                "output": out,
                "value": result,
                "data": {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_crypto(
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
        C: prepare
        A: prepare_crypto
        P:
          kind: str                         # rsa_sign | rsa_verify | rsa_encrypt | rsa_decrypt
          output: str                       # 输出变量名
          input_value: any=None             # sign / encrypt / verify 原文输入
          private_key: str?=None            # sign / decrypt 私钥；支持完整 PEM 或裸 key 字符串
          public_key: str?=None             # verify / encrypt 公钥；支持完整 PEM 或裸 key 字符串
          signature: any=None               # verify 输入签名
          ciphertext: any=None              # decrypt 输入密文
          encoding: str="utf-8"             # 文本编码
          out_mode: str="base64"            # sign / encrypt 输出格式：base64 | hex | bytes
          signature_format: str="base64"    # verify 输入签名格式：base64 | hex | bytes
          ciphertext_format: str="base64"   # decrypt 输入密文格式：base64 | hex | bytes
          encrypt_padding: str="oaep"       # encrypt 填充：oaep | pkcs1v15
          decrypt_padding: str="oaep"       # decrypt 填充：oaep | pkcs1v15
          sign_padding: str="pkcs1v15"      # sign 填充：pkcs1v15 | pss
          verify_padding: str="pkcs1v15"    # verify 填充：pkcs1v15 | pss
          algorithm: str="sha256"           # 摘要算法：sha1 | sha224 | sha256 | sha384 | sha512
          mgf_algorithm: str?=None          # OAEP/PSS 的 MGF1 摘要算法；未填则跟随 algorithm
          label: str?=None                  # OAEP label；未填则为 None
          salt_length: str="max"            # PSS salt 长度：max | digest | auto | 整数文本
        R: CTR
        N:
          - 用于 RSA 签名、验签、公钥加密、私钥解密
          - key 支持完整 PEM，也支持裸字符串，内部会自动补 BEGIN/END
          - 私钥兼容 PKCS8 / PKCS1；公钥兼容 SPKI / PKCS1
          - 加密/解密填充可配置：OAEP 或 PKCS1v15
          - 签名/验签填充可配置：PKCS1v15 或 PSS
          - OAEP / PSS 支持自定义摘要算法与 MGF1 摘要算法
          - salt_length=auto 仅用于 rsa_verify + pss
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _as_text(value_in: typing.Any) -> str:
            if value_in is None:
                return ""
            if isinstance(value_in, str):
                return value_in
            if isinstance(value_in, bytes):
                return value_in.decode(encoding)
            if isinstance(value_in, bytearray):
                return bytes(value_in).decode(encoding)
            return str(value_in)

        def _as_bytes(value_in: typing.Any) -> bytes:
            if value_in is None:
                return b""
            if isinstance(value_in, bytes):
                return value_in
            if isinstance(value_in, bytearray):
                return bytes(value_in)
            if isinstance(value_in, str):
                return value_in.encode(encoding)
            return str(value_in).encode(encoding)

        def _normalize_pem_key(key_value: str, pem_types: list[str]) -> str:
            normalized = str(key_value or "").strip()
            if not normalized:
                raise ValueError("key is required")

            if "-----BEGIN " in normalized and "-----END " in normalized:
                return normalized

            body_only = "".join(normalized.split())
            if not body_only:
                raise ValueError("key is empty")

            head_type = pem_types[0]
            chunks = [body_only[i:i + 64] for i in range(0, len(body_only), 64)]
            return (
                f"-----BEGIN {head_type}-----\n"
                + "\n".join(chunks)
                + f"\n-----END {head_type}-----\n"
            )

        def _load_rsa_private_key(key_value: str) -> rsa.RSAPrivateKey:
            last_error: Exception | None = None
            for pem_text in (
                    _normalize_pem_key(key_value, ["PRIVATE KEY"]),
                    _normalize_pem_key(key_value, ["RSA PRIVATE KEY"])
            ):
                try:
                    loaded_key = serialization.load_pem_private_key(
                        _as_bytes(pem_text), password=None
                    )
                    if not isinstance(loaded_key, rsa.RSAPrivateKey):
                        raise ValueError("private_key is not RSA private key")
                    return loaded_key
                except Exception as exc:
                    last_error = exc

            raise ValueError(f"private_key load failed: {last_error}")

        def _load_rsa_public_key(key_value: str) -> rsa.RSAPublicKey:
            last_error: Exception | None = None
            for pem_text in (
                    _normalize_pem_key(key_value, ["PUBLIC KEY"]),
                    _normalize_pem_key(key_value, ["RSA PUBLIC KEY"])
            ):
                try:
                    loaded_key = serialization.load_pem_public_key(
                        _as_bytes(pem_text)
                    )
                    if not isinstance(loaded_key, rsa.RSAPublicKey):
                        raise ValueError("public_key is not RSA public key")
                    return loaded_key
                except Exception as exc:
                    last_error = exc

            raise ValueError(f"public_key load failed: {last_error}")

        def _encode_result(binary_value: bytes, mode_value: str) -> typing.Any:
            mode_norm = str(mode_value or "base64").strip().lower()
            if mode_norm == "bytes":
                return binary_value
            if mode_norm == "hex":
                return binary_value.hex()
            if mode_norm == "base64":
                return base64.b64encode(binary_value).decode("ascii")
            raise ValueError(f"unsupported out_mode: {mode_value}")

        def _decode_result(input_data: typing.Any, mode_value: str) -> bytes:
            mode_norm = str(mode_value or "base64").strip().lower()
            if mode_norm == "bytes":
                return _as_bytes(input_data)
            if mode_norm == "hex":
                try:
                    return bytes.fromhex(_as_text(input_data).strip())
                except ValueError as exc:
                    raise ValueError("invalid hex input") from exc
            if mode_norm == "base64":
                try:
                    return base64.b64decode(_as_text(input_data).strip(), validate=True)
                except Exception as exc:
                    raise ValueError("invalid base64 input") from exc
            raise ValueError(f"unsupported input format: {mode_value}")

        def _resolve_hash(hash_name: str | None) -> hashes.HashAlgorithm:
            hash_norm = str(hash_name or "sha256").strip().lower()
            mapping: dict[str, type[hashes.HashAlgorithm]] = {
                "sha1": hashes.SHA1,
                "sha224": hashes.SHA224,
                "sha256": hashes.SHA256,
                "sha384": hashes.SHA384,
                "sha512": hashes.SHA512,
            }
            hash_cls = mapping.get(hash_norm)
            if hash_cls is None:
                raise ValueError(f"unsupported hash algorithm: {hash_name}")
            return hash_cls()

        def _resolve_sign_salt_length(salt_value: str, hash_obj: hashes.HashAlgorithm) -> int:
            salt_norm = str(salt_value or "max").strip().lower()
            if salt_norm == "max":
                return padding.PSS.MAX_LENGTH
            if salt_norm == "digest":
                return hash_obj.digest_size
            if salt_norm == "auto":
                raise ValueError("salt_length=auto is only supported for rsa_verify")
            try:
                salt_int = int(salt_norm)
            except ValueError as exc:
                raise ValueError(f"unsupported salt_length: {salt_value}") from exc
            if salt_int < 0:
                raise ValueError("salt_length must be >= 0")
            return salt_int

        def _resolve_verify_salt_length(salt_value: str, hash_obj: hashes.HashAlgorithm) -> int:
            salt_norm = str(salt_value or "max").strip().lower()
            if salt_norm == "max":
                return padding.PSS.MAX_LENGTH
            if salt_norm == "digest":
                return hash_obj.digest_size
            if salt_norm == "auto":
                return padding.PSS.AUTO
            try:
                salt_int = int(salt_norm)
            except ValueError as exc:
                raise ValueError(f"unsupported salt_length: {salt_value}") from exc
            if salt_int < 0:
                raise ValueError("salt_length must be >= 0")
            return salt_int

        def _build_encrypt_pad() -> padding.AsymmetricPadding:
            pad_norm = str(encrypt_padding or "oaep").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)

            if pad_norm == "oaep":
                return padding.OAEP(
                    mgf=padding.MGF1(algorithm=mgf_algo_obj),
                    algorithm=algo_obj,
                    label=None if label is None else _as_bytes(label)
                )
            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            raise ValueError(f"unsupported encrypt_padding: {encrypt_padding}")

        def _build_decrypt_pad() -> padding.AsymmetricPadding:
            pad_norm = str(decrypt_padding or "oaep").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)

            if pad_norm == "oaep":
                return padding.OAEP(
                    mgf=padding.MGF1(algorithm=mgf_algo_obj),
                    algorithm=algo_obj,
                    label=None if label is None else _as_bytes(label)
                )
            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            raise ValueError(f"unsupported decrypt_padding: {decrypt_padding}")

        def _build_sign_pad() -> padding.AsymmetricPadding:
            pad_norm = str(sign_padding or "pkcs1v15").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)

            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            if pad_norm == "pss":
                return padding.PSS(
                    mgf=padding.MGF1(mgf_algo_obj),
                    salt_length=_resolve_sign_salt_length(salt_length, algo_obj)
                )
            raise ValueError(f"unsupported sign_padding: {sign_padding}")

        def _build_verify_pad() -> padding.AsymmetricPadding:
            pad_norm = str(verify_padding or "pkcs1v15").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)

            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            if pad_norm == "pss":
                return padding.PSS(
                    mgf=padding.MGF1(mgf_algo_obj),
                    salt_length=_resolve_verify_salt_length(salt_length, algo_obj)
                )
            raise ValueError(f"unsupported verify_padding: {verify_padding}")

        if tp == "rsa_sign":
            if not private_key:
                raise ValueError("private_key is required")

            signer_key = _load_rsa_private_key(private_key)
            message_bytes = _as_bytes(input_value)
            sign_hash = _resolve_hash(algorithm)
            sign_pad_obj = _build_sign_pad()

            signature_bytes = signer_key.sign(
                message_bytes, sign_pad_obj, sign_hash
            )
            result: typing.Any = _encode_result(signature_bytes, out_mode)

        elif tp == "rsa_verify":
            if not public_key:
                raise ValueError("public_key is required")
            if signature is None:
                raise ValueError("signature is required")

            verifier_key = _load_rsa_public_key(public_key)
            message_bytes = _as_bytes(input_value)
            signature_bytes = _decode_result(signature, signature_format)
            verify_hash = _resolve_hash(algorithm)
            verify_pad_obj = _build_verify_pad()

            try:
                verifier_key.verify(
                    signature_bytes, message_bytes, verify_pad_obj, verify_hash
                )
                result = True
            except InvalidSignature:
                result = False

        elif tp == "rsa_encrypt":
            if not public_key:
                raise ValueError("public_key is required")

            encrypt_key = _load_rsa_public_key(public_key)
            plaintext_bytes = _as_bytes(input_value)
            encrypt_pad_obj = _build_encrypt_pad()

            encrypted_bytes = encrypt_key.encrypt(
                plaintext_bytes, encrypt_pad_obj
            )
            result = _encode_result(encrypted_bytes, out_mode)

        elif tp == "rsa_decrypt":
            if not private_key:
                raise ValueError("private_key is required")
            if ciphertext is None:
                raise ValueError("ciphertext is required")

            decrypt_key = _load_rsa_private_key(private_key)
            ciphertext_bytes = _decode_result(ciphertext, ciphertext_format)
            decrypt_pad_obj = _build_decrypt_pad()

            plaintext_bytes = decrypt_key.decrypt(
                ciphertext_bytes, decrypt_pad_obj
            )

            try:
                result = plaintext_bytes.decode(encoding)
            except UnicodeDecodeError:
                result = plaintext_bytes

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_crypto ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind": tp,
                "output": out,
                "value": result,
                "data": {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_aes(
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
        C: prepare
        A: prepare_aes
        P:
          kind: str                         # aes_encrypt | aes_decrypt
          output: str                       # 输出变量名
          input_value: any=None             # 输入明文或密文
          key: any=None                     # AES key
          iv: any=None                      # CBC 用 IV
          nonce: any=None                   # GCM 用 nonce
          aad: any=None                     # GCM 附加认证数据
          tag: any=None                     # GCM 解密 tag
          mode: str="cbc"                   # cbc | ecb | gcm
          key_format: str="text"            # text | base64 | hex | bytes
          input_format: str="text"          # text | base64 | hex | bytes
          iv_format: str="text"             # text | base64 | hex | bytes
          nonce_format: str="text"          # text | base64 | hex | bytes
          aad_format: str="text"            # text | base64 | hex | bytes
          tag_format: str="base64"          # text | base64 | hex | bytes
          out_mode: str="base64"            # text | base64 | hex | bytes
          encoding: str="utf-8"             # 文本编码
          padding_mode: str="pkcs7"         # cbc/ecb 可用：pkcs7 | none
        R: CTR
        N:
          - 用于 AES CBC / ECB / GCM 加解密
          - GCM 加密返回 {ciphertext, tag}
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        mode_v = str(mode or "cbc").strip().lower()
        pad_v = str(padding_mode or "pkcs7").strip().lower()
        if not out:
            raise ValueError("output is required")
        if key is None:
            raise ValueError("key is required")

        def _to_text(v: typing.Any) -> str:
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if isinstance(v, bytes):
                return v.decode(encoding)
            if isinstance(v, bytearray):
                return bytes(v).decode(encoding)
            return str(v)

        def _to_bytes(v: typing.Any) -> bytes:
            if v is None:
                return b""
            if isinstance(v, bytes):
                return v
            if isinstance(v, bytearray):
                return bytes(v)
            if isinstance(v, str):
                return v.encode(encoding)
            return str(v).encode(encoding)

        def _decode(v: typing.Any, fmt: str) -> bytes:
            fmt_v = str(fmt or "text").strip().lower()
            if fmt_v == "bytes":
                return _to_bytes(v)
            if fmt_v == "text":
                return _to_bytes(v)
            if fmt_v == "base64":
                try:
                    return base64.b64decode(_to_text(v).strip(), validate=True)
                except Exception as exc:
                    raise ValueError("invalid base64 input") from exc
            if fmt_v == "hex":
                try:
                    return bytes.fromhex(_to_text(v).strip())
                except ValueError as exc:
                    raise ValueError("invalid hex input") from exc
            raise ValueError(f"unsupported format: {fmt}")

        def _encode(v: bytes, fmt: str) -> typing.Any:
            fmt_v = str(fmt or "base64").strip().lower()
            if fmt_v == "bytes":
                return v
            if fmt_v == "base64":
                return base64.b64encode(v).decode("ascii")
            if fmt_v == "hex":
                return v.hex()
            if fmt_v == "text":
                return v.decode(encoding)
            raise ValueError(f"unsupported out_mode: {fmt}")

        def _build_cipher(key_content: bytes, tag_content: bytes | None = None) -> Cipher:
            if len(key_content) not in (16, 24, 32):
                raise ValueError("AES key length must be 16/24/32 bytes")

            if mode_v == "cbc":
                iv_bytes = _decode(iv, iv_format)
                if len(iv_bytes) != 16:
                    raise ValueError("CBC iv length must be 16 bytes")
                return Cipher(algorithms.AES(key_content), modes.CBC(iv_bytes))

            if mode_v == "ecb":
                return Cipher(algorithms.AES(key_content), modes.ECB())

            if mode_v == "gcm":
                nonce_bytes = _decode(nonce, nonce_format)
                if not nonce_bytes:
                    raise ValueError("nonce is required for gcm")
                return Cipher(
                    algorithms.AES(key_content),
                    modes.GCM(nonce_bytes, tag_content)
                    if tag_content is not None else modes.GCM(nonce_bytes)
                )

            raise ValueError(f"unsupported mode: {mode}")

        def _pad_plain(plain: bytes) -> bytes:
            if mode_v == "gcm":
                return plain
            if pad_v == "none":
                if len(plain) % 16 != 0:
                    raise ValueError("input length must be multiple of 16 when padding_mode=none")
                return plain
            if pad_v == "pkcs7":
                padder = sym_padding.PKCS7(128).padder()
                return padder.update(plain) + padder.finalize()
            raise ValueError(f"unsupported padding_mode: {padding_mode}")

        def _unpad_plain(plain: bytes) -> bytes:
            if mode_v == "gcm":
                return plain
            if pad_v == "none":
                return plain
            if pad_v == "pkcs7":
                unpadder = sym_padding.PKCS7(128).unpadder()
                return unpadder.update(plain) + unpadder.finalize()
            raise ValueError(f"unsupported padding_mode: {padding_mode}")

        key_bytes = _decode(key, key_format)

        if tp == "aes_encrypt":
            plain_bytes = _decode(input_value, input_format)
            cipher = _build_cipher(key_bytes)
            encryptor = cipher.encryptor()

            if mode_v == "gcm":
                aad_bytes = _decode(aad, aad_format) if aad is not None else b""
                if aad_bytes:
                    encryptor.authenticate_additional_data(aad_bytes)
                cipher_bytes = encryptor.update(plain_bytes) + encryptor.finalize()
                result: typing.Any = {
                    "ciphertext": _encode(cipher_bytes, out_mode),
                    "tag": _encode(encryptor.tag, out_mode)
                }
            else:
                padded_bytes = _pad_plain(plain_bytes)
                cipher_bytes = encryptor.update(padded_bytes) + encryptor.finalize()
                result = _encode(cipher_bytes, out_mode)

        elif tp == "aes_decrypt":
            if mode_v == "gcm":
                if tag is None:
                    raise ValueError("tag is required for gcm decrypt")
                tag_bytes = _decode(tag, tag_format)
                cipher = _build_cipher(key_bytes, tag_bytes)
            else:
                cipher = _build_cipher(key_bytes)

            cipher_bytes = _decode(input_value, input_format)
            decryptor = cipher.decryptor()

            if mode_v == "gcm":
                aad_bytes = _decode(aad, aad_format) if aad is not None else b""
                if aad_bytes:
                    decryptor.authenticate_additional_data(aad_bytes)
                plain_bytes = decryptor.update(cipher_bytes) + decryptor.finalize()
            else:
                padded_plain_bytes = decryptor.update(cipher_bytes) + decryptor.finalize()
                plain_bytes = _unpad_plain(padded_plain_bytes)

            result = _encode(plain_bytes, out_mode)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_aes ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_url(
        kind: str,
        output: str,
        input_value: typing.Any = None,
        data: typing.Optional[dict[str, typing.Any]] = None,
        encoding: str = "utf-8",
        safe: str = "",
        doseq: bool = True,
        plus_for_space: bool = True,
        keep_blank_values: bool = True
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_url
        P:
          kind: str                         # url_encode | url_decode | form_urlencode | form_urldecode
          output: str                       # 输出变量名
          input_value: any=None             # url_encode / url_decode / form_urldecode 输入
          data: dict?=None                  # form_urlencode 输入
          encoding: str="utf-8"             # 文本编码
          safe: str=""                      # url_encode 安全字符
          doseq: bool=True                  # form_urlencode 是否展开 list
          plus_for_space: bool=True         # form_urlencode 是否空格转 +
          keep_blank_values: bool=True      # form_urldecode 是否保留空值
        R: CTR
        N:
          - 用于 URL 编码、解码、表单编码、表单解码
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _to_text(v: typing.Any) -> str:
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if isinstance(v, bytes):
                return v.decode(encoding)
            if isinstance(v, bytearray):
                return bytes(v).decode(encoding)
            return str(v)

        if tp == "url_encode":
            result: typing.Any = quote(_to_text(input_value), safe=str(safe or ""), encoding=encoding)

        elif tp == "url_decode":
            result = unquote(_to_text(input_value), encoding=encoding)

        elif tp == "form_urlencode":
            if not isinstance(data, dict):
                raise ValueError("data must be dict")

            quote_via = quote if not plus_for_space else None
            result = urlencode(
                data,
                doseq=bool(doseq),
                encoding=encoding,
                safe=str(safe or ""),
                quote_via=quote_via
            ) if quote_via is not None else urlencode(
                data,
                doseq=bool(doseq),
                encoding=encoding,
                safe=str(safe or "")
            )

        elif tp == "form_urldecode":
            parsed = parse_qs(
                _to_text(input_value),
                keep_blank_values=bool(keep_blank_values),
                encoding=encoding
            )
            result = {
                k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in parsed.items()
            }

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_url ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_value(
        kind: str,
        output: str,
        value: typing.Any = None,
        source: typing.Any = None,
        path: str | None = None,
        values: typing.Optional[list[typing.Any]] = None,
        default: typing.Any = None
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_value
        P:
          kind: str                    # const | pick | coalesce
          output: str                  # 输出变量名
          value: any=None              # const 输入值
          source: any=None             # pick 的源对象
          path: str?=None              # pick 路径，如 a.b.0.c
          values: list[any]?=None      # coalesce 候选值列表
          default: any=None            # coalesce 默认值
        R: CTR
        N:
          - 用于常量注入、上下文取值、回退取值
          - pick 按 a.b.0.c 路径取值
          - coalesce 返回第一个非空值
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _pick(query: typing.Any, pick_path: str) -> typing.Any:
            if not pick_path:
                return query

            cur = query
            for seg in str(pick_path).split("."):
                if seg == "":
                    continue

                if isinstance(cur, dict):
                    if seg not in cur:
                        raise KeyError(seg)
                    cur = cur[seg]
                    continue

                if isinstance(cur, list):
                    idx = int(seg)
                    cur = cur[idx]
                    continue

                raise KeyError(seg)

            return cur

        if tp == "const":
            result: typing.Any = value

        elif tp == "pick":
            p = str(path or "").strip()
            if not p:
                raise ValueError("path is required")
            result = _pick(source, p)

        elif tp == "coalesce":
            if not isinstance(values, list):
                raise ValueError("values must be list")
            result = default
            for one in values:
                if one not in (None, "", [], {}, ()):
                    result = one
                    break

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_value ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_text(
        kind: str,
        output: str,
        template: str | None = None,
        mapping: typing.Optional[dict[str, typing.Any]] = None,
        items: typing.Optional[list[typing.Any]] = None,
        sep: str = ""
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_text
        P:
          kind: str                    # format | concat | join
          output: str                  # 输出变量名
          template: str?=None          # format 模板文本，使用 Python format 风格，如 "Bearer {token}"
          mapping: dict[str,any]?=None # format 映射参数
          items: list[any]?=None       # concat / join 输入列表
          sep: str=""                  # 拼接分隔符
        R: CTR
        N:
          - 用于轻量文本拼装
          - format 使用 template.format(**mapping) 生成结果
          - concat / join 都会把元素转成字符串后拼接
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        if tp == "format":
            if template is None:
                raise ValueError("template is required")
            mp = mapping or {}
            if not isinstance(mp, dict):
                raise ValueError("mapping must be dict")
            try:
                result: typing.Any = str(template).format(**mp)
            except KeyError as e:
                raise ValueError(f"missing format key: {e}") from e
            except Exception as e:
                raise ValueError(f"format failed: {e}") from e

        elif tp == "concat":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            result = "".join("" if x is None else str(x) for x in items)

        elif tp == "join":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            result = str(sep or "").join("" if x is None else str(x) for x in items)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_text ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_sign_text(
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
        C: prepare
        A: prepare_sign_text
        P:
          kind: str                         # key_value_join | query_like | body_plus_secret | prefix_suffix
          output: str                       # 输出变量名
          data: dict?=None                  # key_value_join / query_like 输入
          items: list?=None                 # body_plus_secret / prefix_suffix 输入
          secret: any=None                  # body_plus_secret 密钥
          prefix: str=""                    # 前缀
          suffix: str=""                    # 后缀
          pair_sep: str="&"                 # 键值对分隔符
          kv_sep: str="="                   # 键值分隔符
          sort_keys: bool=True              # 是否按 key 排序
          ignore_empty: bool=True           # 是否忽略空值
          ignore_keys: list[str]?=None      # 忽略字段名
        R: CTR
        N:
          - 用于常见签名串拼装
          - 可忽略空值、忽略指定字段、按 key 排序
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        ignore_key_set = {str(x) for x in (ignore_keys or [])}

        def _is_empty(v: typing.Any) -> bool:
            return v in (None, "", [], {}, ())

        def _normalize_pairs(obj: dict[str, typing.Any]) -> list[tuple[str, str]]:
            _pairs: list[tuple[str, str]] = []
            for k, v in obj.items():
                key_s = "" if k is None else str(k)
                if key_s in ignore_key_set:
                    continue

                if isinstance(v, list):
                    for one in v:
                        if ignore_empty and _is_empty(one):
                            continue
                        _pairs.append((key_s, "" if one is None else str(one)))
                else:
                    if ignore_empty and _is_empty(v):
                        continue
                    _pairs.append((key_s, "" if v is None else str(v)))

            if sort_keys:
                _pairs.sort(key=lambda x: (x[0], x[1]))
            return _pairs

        if tp == "key_value_join":
            if not isinstance(data, dict):
                raise ValueError("data must be dict")
            pairs = _normalize_pairs(data)
            result: typing.Any = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in pairs)

        elif tp == "query_like":
            if not isinstance(data, dict):
                raise ValueError("data must be dict")
            pairs = _normalize_pairs(data)
            result = pair_sep.join(
                f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs
            )

        elif tp == "body_plus_secret":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            body = "".join("" if x is None else str(x) for x in items)
            secret_text = "" if secret is None else str(secret)
            result = f"{prefix}{body}{suffix}{secret_text}"

        elif tp == "prefix_suffix":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            body = "".join("" if x is None else str(x) for x in items)
            result = f"{prefix}{body}{suffix}"

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_sign_text ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_multipart_sign(
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
        C: prepare
        A: prepare_multipart_sign
        P:
          kind: str                         # multipart_field_text | multipart_file_text | multipart_all_text
          output: str                       # 输出变量名
          fields: dict?=None                # 表单字段
          files: list[dict]?=None           # 文件列表：[{field, filename, content_type, value}]
          pair_sep: str="&"                 # 键值对分隔符
          kv_sep: str="="                   # 键值分隔符
          sort_keys: bool=True              # 是否排序
          ignore_empty: bool=True           # 是否忽略空值
          ignore_keys: list[str]?=None      # 忽略字段名
          use_filename_only: bool=True      # 文件是否只取 filename
          include_content_type: bool=False  # 文件签名文本是否包含 content_type
        R: CTR
        N:
          - 用于 multipart/form-data 场景的签名辅助文本生成
          - 不负责真实 multipart 编码，只负责签名前串构造
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        ignore_key_set = {str(x) for x in (ignore_keys or [])}

        def _is_empty(v: typing.Any) -> bool:
            return v in (None, "", [], {}, ())

        def _normalize_field_pairs(obj: dict[str, typing.Any]) -> list[tuple[str, str]]:
            pairs: list[tuple[str, str]] = []
            for k, v in obj.items():
                key_s = "" if k is None else str(k)
                if key_s in ignore_key_set:
                    continue

                if isinstance(v, list):
                    for one in v:
                        if ignore_empty and _is_empty(one):
                            continue
                        pairs.append((key_s, "" if one is None else str(one)))
                else:
                    if ignore_empty and _is_empty(v):
                        continue
                    pairs.append((key_s, "" if v is None else str(v)))

            if sort_keys:
                pairs.sort(key=lambda x: (x[0], x[1]))
            return pairs

        def _normalize_file_pairs(file_items: list[dict[str, typing.Any]]) -> list[tuple[str, str]]:
            pairs: list[tuple[str, str]] = []
            for one in file_items:
                if not isinstance(one, dict):
                    raise ValueError("files must contain dict only")

                field_name = "" if one.get("field") is None else str(one.get("field"))
                if field_name in ignore_key_set:
                    continue

                filename = "" if one.get("filename") is None else str(one.get("filename"))
                content_type = "" if one.get("content_type") is None else str(one.get("content_type"))
                value = one.get("value")

                if use_filename_only:
                    file_text = filename
                else:
                    file_text = "" if value is None else str(value)

                if include_content_type:
                    file_text = f"{file_text}:{content_type}"

                if ignore_empty and _is_empty(file_text):
                    continue

                pairs.append((field_name, file_text))

            if sort_keys:
                pairs.sort(key=lambda x: (x[0], x[1]))
            return pairs

        field_pairs = _normalize_field_pairs(fields or {})
        file_pairs = _normalize_file_pairs(files or [])

        if tp == "multipart_field_text":
            result: typing.Any = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in field_pairs)

        elif tp == "multipart_file_text":
            result = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in file_pairs)

        elif tp == "multipart_all_text":
            all_pairs = field_pairs + file_pairs
            if sort_keys:
                all_pairs.sort(key=lambda x: (x[0], x[1]))
            result = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in all_pairs)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_multipart_sign ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_time(
        kind: str,
        output: str,
        timestamp: int | float | None = None,
        unit: str = "s",
        fmt: str = "%Y-%m-%dT%H:%M:%SZ",
        offset_seconds: int = 0,
        offset_minutes: int = 0,
        offset_hours: int = 0,
        utc: bool = True
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_time
        P:
          kind: str                         # now_iso | format_ts | offset_ts
          output: str                       # 输出变量名
          timestamp: int|float?=None        # format_ts / offset_ts 输入时间戳
          unit: str="s"                     # s | ms
          fmt: str="%Y-%m-%dT%H:%M:%SZ"     # 输出格式
          offset_seconds: int=0             # 偏移秒
          offset_minutes: int=0             # 偏移分钟
          offset_hours: int=0               # 偏移小时
          utc: bool=True                    # 是否使用 UTC
        R: CTR
        N:
          - 用于当前时间格式化、时间戳格式化、偏移时间生成
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        unit_v = str(unit or "s").strip().lower()
        if not out:
            raise ValueError("output is required")

        tz = timezone.utc if utc else None

        def _to_datetime(ts: int | float | None) -> datetime:
            base_ts = time.time() if ts is None else float(ts)
            if unit_v == "ms":
                base_ts = base_ts / 1000.0
            elif unit_v != "s":
                raise ValueError(f"unsupported unit: {unit}")
            return datetime.fromtimestamp(base_ts, tz=tz)

        delta = timedelta(
            seconds=int(offset_seconds),
            minutes=int(offset_minutes),
            hours=int(offset_hours)
        )

        if tp == "now_iso":
            dt = datetime.now(tz=tz) + delta
            result: typing.Any = dt.strftime(fmt)

        elif tp == "format_ts":
            if timestamp is None:
                raise ValueError("timestamp is required")
            dt = _to_datetime(timestamp)
            result = dt.strftime(fmt)

        elif tp == "offset_ts":
            dt = _to_datetime(timestamp) + delta
            if unit_v == "ms":
                result = int(dt.timestamp() * 1000)
            else:
                result = int(dt.timestamp())

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_time ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_compress(
        kind: str,
        output: str,
        input_value: typing.Any,
        input_format: str = "text",
        out_mode: str = "base64",
        encoding: str = "utf-8",
        compress_level: int = 9,
        wbits: int = zlib.MAX_WBITS
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_compress
        P:
          kind: str                         # gzip_encode | gzip_decode | zlib_encode | zlib_decode
          output: str                       # 输出变量名
          input_value: any                  # 输入值
          input_format: str="text"          # text | base64 | hex | bytes
          out_mode: str="base64"            # text | base64 | hex | bytes
          encoding: str="utf-8"             # 文本编码
          compress_level: int=9             # 压缩等级
          wbits: int=zlib.MAX_WBITS         # zlib 窗口参数
        R: CTR
        N:
          - 用于 gzip / zlib 压缩与解压
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _to_text(v: typing.Any) -> str:
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if isinstance(v, bytes):
                return v.decode(encoding)
            if isinstance(v, bytearray):
                return bytes(v).decode(encoding)
            return str(v)

        def _to_bytes(v: typing.Any) -> bytes:
            if v is None:
                return b""
            if isinstance(v, bytes):
                return v
            if isinstance(v, bytearray):
                return bytes(v)
            if isinstance(v, str):
                return v.encode(encoding)
            return str(v).encode(encoding)

        def _decode(v: typing.Any, fmt: str) -> bytes:
            fmt_v = str(fmt or "text").strip().lower()
            if fmt_v == "bytes":
                return _to_bytes(v)
            if fmt_v == "text":
                return _to_bytes(v)
            if fmt_v == "base64":
                try:
                    return base64.b64decode(_to_text(v).strip(), validate=True)
                except Exception as exc:
                    raise ValueError("invalid base64 input") from exc
            if fmt_v == "hex":
                try:
                    return bytes.fromhex(_to_text(v).strip())
                except ValueError as exc:
                    raise ValueError("invalid hex input") from exc
            raise ValueError(f"unsupported input_format: {fmt}")

        def _encode(v: bytes, fmt: str) -> typing.Any:
            fmt_v = str(fmt or "base64").strip().lower()
            if fmt_v == "bytes":
                return v
            if fmt_v == "base64":
                return base64.b64encode(v).decode("ascii")
            if fmt_v == "hex":
                return v.hex()
            if fmt_v == "text":
                return v.decode(encoding)
            raise ValueError(f"unsupported out_mode: {fmt}")

        source_bytes = _decode(input_value, input_format)

        if tp == "gzip_encode":
            compressed = gzip.compress(source_bytes, compresslevel=int(compress_level))
            result: typing.Any = _encode(compressed, out_mode)

        elif tp == "gzip_decode":
            decompressed = gzip.decompress(source_bytes)
            result = _encode(decompressed, out_mode)

        elif tp == "zlib_encode":
            compressed = zlib.compress(source_bytes, level=int(compress_level))
            result = _encode(compressed, out_mode)

        elif tp == "zlib_decode":
            decompressed = zlib.decompress(source_bytes, wbits=int(wbits))
            result = _encode(decompressed, out_mode)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_compress ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "prepare"})
    def prepare_struct(
        kind: str,
        output: str,
        inputs: typing.Optional[list[typing.Any]] = None,
        input_value: typing.Any = None
    ) -> CallToolResult:
        """
        D: common
        C: prepare
        A: prepare_struct
        P:
          kind: str                         # dict_merge | sort_keys | canonical_query
          output: str                       # 输出变量名
          inputs: list[any]?=None           # dict_merge 输入列表
          input_value: any=None             # 单输入对象
        R: CTR
        N:
          - 用于结构对象拼装与稳定化处理
          - dict_merge 按顺序合并，后者覆盖前者
          - sort_keys 返回 key 排序后的 dict
          - canonical_query 将 dict 转成稳定 query string
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        def _dict_merge(items_v: list[typing.Any]) -> dict[str, typing.Any]:
            merged: dict[str, typing.Any] = {}
            for one in items_v:
                if not isinstance(one, dict):
                    raise ValueError("inputs must contain dict only")
                merged.update(one)
            return merged

        def _canonical_query(query: typing.Any) -> str:
            if query is None:
                return ""
            if not isinstance(query, dict):
                raise ValueError("input_value must be dict")

            pairs: list[tuple[str, str]] = []
            for k, v in query.items():
                key = "" if k is None else str(k)
                if isinstance(v, list):
                    for _one in v:
                        pairs.append((key, "" if _one is None else str(_one)))
                else:
                    pairs.append((key, "" if v is None else str(v)))

            pairs.sort(key=lambda x: (x[0], x[1]))
            return "&".join(
                f"{quote(k, safe='')}={quote(v, safe='')}"
                for k, v in pairs
            )

        if tp == "dict_merge":
            if not isinstance(inputs, list):
                raise ValueError("inputs must be list")
            result: typing.Any = _dict_merge(inputs)

        elif tp == "sort_keys":
            if not isinstance(input_value, dict):
                raise ValueError("input_value must be dict")
            result = {k: input_value[k] for k in sorted(input_value.keys(), key=lambda x: str(x))}

        elif tp == "canonical_query":
            result = _canonical_query(input_value)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        text = f"prepare_struct ok kind={tp} output={out}"
        structured: typing.Any = {
            "text": text,
            "data": {
                "kind"   : tp,
                "output" : out,
                "value"  : result,
                "data"   : {out: result}
            }
        }

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured
        )


if __name__ == '__main__':
    pass
