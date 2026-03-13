#  ____
# |  _ \ _ __ ___ _ __   __ _ _ __ ___
# | |_) | '__/ _ \ '_ \ / _` | '__/ _ \
# |  __/| | |  __/ |_) | (_| | | |  __/
# |_|   |_|  \___| .__/ \__,_|_|  \___|
#                |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import jwt
import hmac
import json
import time
import uuid
import base64
import string
import typing
import secrets
import hashlib
from urllib.parse import quote
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import (
    hashes, serialization
)
from cryptography.hazmat.primitives.asymmetric import (
    padding, rsa
)
from mcp.server import FastMCP
from mcp.types import (
    CallToolResult, TextContent
)


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
            decoded = base64.b64decode(raw, validate=False)
            result = decoded.decode(encoding) if as_text else decoded

        elif tp == "base64url_encode":
            raw = base64.urlsafe_b64encode(_to_bytes(input_value)).decode("ascii")
            result = raw.rstrip("=") if strip_padding else raw

        elif tp == "base64url_decode":
            raw = _to_text(input_value).strip()
            pad = (-len(raw)) % 4
            if pad:
                raw += "=" * pad
            decoded = base64.urlsafe_b64decode(raw)
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
          private_key: str?=None            # jwt_rs256 私钥 PEM
          public_key: str?=None             # jwt_verify_rs256 公钥 PEM
          headers: dict?=None               # jwt_rs256 headers
          options: dict?=None               # jwt_verify_rs256 options
          return_payload: bool=True         # verify 时返回 payload，否则返回 True
        R: CTR
        N:
          - 用于 JWT RS256 的生成与验签解析
          - 私钥 / 公钥均要求 PEM 文本
        """

        tp = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        if tp == "jwt_rs256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not private_key:
                raise ValueError("private_key is required")

            result: typing.Any = jwt.encode(
                payload,
                private_key,
                algorithm="RS256",
                headers=headers
            )

        elif tp == "jwt_verify_rs256":
            if not token:
                raise ValueError("token is required")
            if not public_key:
                raise ValueError("public_key is required")

            decoded = jwt.decode(
                token,
                public_key,
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
