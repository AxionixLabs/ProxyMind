import jwt
import hmac
import base64
import typing
import hashlib
from urllib.parse import quote
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import (
    hashes, serialization
)
from cryptography.hazmat.primitives.asymmetric import (
    ec, padding, rsa
)
from cryptography.hazmat.primitives.ciphers import (
    Cipher, algorithms, modes
)
from cryptography.hazmat.primitives import padding as sym_padding


class SecurityService(object):
    """承载安全与签名相关的确定性实现，供 security 工具层做薄适配。"""

    @staticmethod
    def digest(
        *,
        kind: str,
        output: str,
        input_value: typing.Any,
        secret: typing.Any = None,
        encoding: str = "utf-8",
        out_mode: str = "hex"
    ) -> dict[str, typing.Any]:
        """执行摘要或 HMAC 计算，并返回统一结果结构。"""

        tp   = str(kind or "").strip().lower()
        out  = str(output or "").strip()
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

        if tp in {"md5", "sha1", "sha224", "sha256", "sha384", "sha512"}:
            h = hashlib.new(tp)
            h.update(data_in)
            value: typing.Any = _format_digest(h.digest())
        elif tp in {"hmac_md5", "hmac_sha1", "hmac_sha224", "hmac_sha256", "hmac_sha384", "hmac_sha512"}:
            if secret is None:
                raise ValueError("secret is required")
            algo = tp.replace("hmac_", "")
            key = _to_bytes(secret)
            value = _format_digest(hmac.new(key, data_in, getattr(hashlib, algo)).digest())
        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def jwt_hs(
        *,
        kind: str,
        output: str,
        payload: typing.Optional[dict[str, typing.Any]] = None,
        secret: str | None = None,
        token: str | None = None,
        headers: typing.Optional[dict[str, typing.Any]] = None,
        options: typing.Optional[dict[str, typing.Any]] = None,
        return_payload: bool = True,
        complete: bool = False
    ) -> dict[str, typing.Any]:
        """处理 HS256 JWT 的生成、无验签解析与验签。"""

        tp  = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        if tp == "jwt_hs256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not secret:
                raise ValueError("secret is required")
            value: typing.Any = jwt.encode(payload, secret, algorithm="HS256", headers=headers)

        elif tp == "jwt_decode_unverified":
            if not token:
                raise ValueError("token is required")
            value = (
                jwt.decode_complete(token, options={"verify_signature": False})
                if complete
                else jwt.decode(token, options={"verify_signature": False})
            )

        elif tp == "jwt_verify_hs256":
            if not token:
                raise ValueError("token is required")
            if not secret:
                raise ValueError("secret is required")
            decoded = jwt.decode(token, secret, algorithms=["HS256"], options=options or {})
            value = decoded if return_payload else True

        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def jwt_asymmetric(
        *,
        kind: str,
        output: str,
        payload: typing.Optional[dict[str, typing.Any]] = None,
        token: str | None = None,
        private_key: str | None = None,
        public_key: str | None = None,
        headers: typing.Optional[dict[str, typing.Any]] = None,
        options: typing.Optional[dict[str, typing.Any]] = None,
        return_payload: bool = True
    ) -> dict[str, typing.Any]:
        """处理 RS256 / ES256 JWT 的生成与验签。"""

        tp  = str(kind or "").strip().lower()
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
            return f"-----BEGIN {head_type}-----\n" + "\n".join(chunks) + f"\n-----END {head_type}-----\n"

        def _normalize_rsa_private_key(key_value: str) -> str:
            last_error: Exception | None = None
            for pem_text in (
                _normalize_pem_key(key_value, ["PRIVATE KEY"]),
                _normalize_pem_key(key_value, ["RSA PRIVATE KEY"]),
            ):
                try:
                    serialization.load_pem_private_key(pem_text.encode("utf-8"), password=None)
                    return pem_text
                except Exception as exc:
                    last_error = exc
            raise ValueError(f"private_key load failed: {last_error}")

        def _normalize_rsa_public_key(key_value: str) -> str:
            last_error: Exception | None = None
            for pem_text in (
                _normalize_pem_key(key_value, ["PUBLIC KEY"]),
                _normalize_pem_key(key_value, ["RSA PUBLIC KEY"]),
            ):
                try:
                    serialization.load_pem_public_key(pem_text.encode("utf-8"))
                    return pem_text
                except Exception as exc:
                    last_error = exc
            raise ValueError(f"public_key load failed: {last_error}")

        def _normalize_ec_private_key(key_value: str) -> str:
            normalized = str(key_value or "").strip()
            if not normalized:
                raise ValueError("private_key is required")
            if "-----BEGIN " not in normalized or "-----END " not in normalized:
                raise ValueError("ec private_key must be full PEM")
            loaded_key = serialization.load_pem_private_key(normalized.encode("utf-8"), password=None)
            if not isinstance(loaded_key, ec.EllipticCurvePrivateKey):
                raise ValueError("private_key is not EC private key")
            return normalized

        def _normalize_ec_public_key(key_value: str) -> str:
            normalized = str(key_value or "").strip()
            if not normalized:
                raise ValueError("public_key is required")
            if "-----BEGIN " not in normalized or "-----END " not in normalized:
                raise ValueError("ec public_key must be full PEM")
            loaded_key = serialization.load_pem_public_key(normalized.encode("utf-8"))
            if not isinstance(loaded_key, ec.EllipticCurvePublicKey):
                raise ValueError("public_key is not EC public key")
            return normalized

        if tp == "jwt_rs256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not private_key:
                raise ValueError("private_key is required")
            value: typing.Any = jwt.encode(
                payload,
                _normalize_rsa_private_key(private_key),
                algorithm="RS256",
                headers=headers,
            )

        elif tp == "jwt_verify_rs256":
            if not token:
                raise ValueError("token is required")
            if not public_key:
                raise ValueError("public_key is required")

            decoded = jwt.decode(
                token,
                _normalize_rsa_public_key(public_key),
                algorithms=["RS256"],
                options=options or {},
            )
            value = decoded if return_payload else True

        elif tp == "jwt_es256":
            if not isinstance(payload, dict):
                raise ValueError("payload must be dict")
            if not private_key:
                raise ValueError("private_key is required")
            value = jwt.encode(
                payload,
                _normalize_ec_private_key(private_key),
                algorithm="ES256",
                headers=headers,
            )

        elif tp == "jwt_verify_es256":
            if not token:
                raise ValueError("token is required")
            if not public_key:
                raise ValueError("public_key is required")

            decoded = jwt.decode(
                token,
                _normalize_ec_public_key(public_key),
                algorithms=["ES256"],
                options=options or {},
            )
            value = decoded if return_payload else True
        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def crypto(
        *,
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
    ) -> dict[str, typing.Any]:
        """执行 RSA 签名、验签、公钥加密和私钥解密。"""

        tp  = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")

        alias_config = {
            "rsa_encrypt_oaep_sha256": {
                "tp": "rsa_encrypt", "encrypt_padding": "oaep",
                "algorithm": "sha256", "mgf_algorithm": "sha256"
            },
            "rsa_decrypt_oaep_sha256": {
                "tp": "rsa_decrypt", "decrypt_padding": "oaep",
                "algorithm": "sha256", "mgf_algorithm": "sha256"
            },
            "rsa_sign_pss_sha256": {
                "tp": "rsa_sign", "sign_padding": "pss",
                "algorithm": "sha256", "mgf_algorithm": "sha256", "salt_length": "digest"
            },
            "rsa_verify_pss_sha256": {
                "tp": "rsa_verify", "verify_padding": "pss",
                "algorithm": "sha256", "mgf_algorithm": "sha256", "salt_length": "digest"
            }
        }
        alias = alias_config.get(tp)
        display_kind = tp
        if alias is not None:
            # 强语义别名先翻译为基础 RSA 动作，再走统一实现。
            tp = typing.cast(str, alias["tp"])
            encrypt_padding = typing.cast(str, alias.get("encrypt_padding", encrypt_padding))
            decrypt_padding = typing.cast(str, alias.get("decrypt_padding", decrypt_padding))
            sign_padding = typing.cast(str, alias.get("sign_padding", sign_padding))
            verify_padding = typing.cast(str, alias.get("verify_padding", verify_padding))
            algorithm = typing.cast(str, alias.get("algorithm", algorithm))
            mgf_algorithm = typing.cast(str | None, alias.get("mgf_algorithm", mgf_algorithm))
            salt_length = typing.cast(str, alias.get("salt_length", salt_length))

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
            return f"-----BEGIN {head_type}-----\n" + "\n".join(chunks) + f"\n-----END {head_type}-----\n"

        def _load_rsa_private_key(key_value: str) -> rsa.RSAPrivateKey:
            last_error: Exception | None = None
            for pem_text in (_normalize_pem_key(key_value, ["PRIVATE KEY"]), _normalize_pem_key(key_value, ["RSA PRIVATE KEY"])):
                try:
                    loaded_key = serialization.load_pem_private_key(_as_bytes(pem_text), password=None)
                    if not isinstance(loaded_key, rsa.RSAPrivateKey):
                        raise ValueError("private_key is not RSA private key")
                    return loaded_key
                except Exception as exc:
                    last_error = exc
            raise ValueError(f"private_key load failed: {last_error}")

        def _load_rsa_public_key(key_value: str) -> rsa.RSAPublicKey:
            last_error: Exception | None = None
            for pem_text in (_normalize_pem_key(key_value, ["PUBLIC KEY"]), _normalize_pem_key(key_value, ["RSA PUBLIC KEY"])):
                try:
                    loaded_key = serialization.load_pem_public_key(_as_bytes(pem_text))
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
                return bytes.fromhex(_as_text(input_data).strip())
            if mode_norm == "base64":
                return base64.b64decode(_as_text(input_data).strip(), validate=True)
            raise ValueError(f"unsupported input format: {mode_value}")

        def _resolve_hash(hash_name: str | None) -> hashes.HashAlgorithm:
            mapping: dict[str, type[hashes.HashAlgorithm]] = {
                "sha1"   : hashes.SHA1,
                "sha224" :  hashes.SHA224,
                "sha256" : hashes.SHA256,
                "sha384" : hashes.SHA384,
                "sha512" : hashes.SHA512
            }
            hash_cls = mapping.get(str(hash_name or "sha256").strip().lower())
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
            salt_int = int(salt_norm)
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
            salt_int = int(salt_norm)
            if salt_int < 0:
                raise ValueError("salt_length must be >= 0")
            return salt_int

        def _build_encrypt_pad() -> padding.AsymmetricPadding:
            pad_norm = str(encrypt_padding or "oaep").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)
            if pad_norm == "oaep":
                return padding.OAEP(mgf=padding.MGF1(algorithm=mgf_algo_obj), algorithm=algo_obj, label=None if label is None else _as_bytes(label))
            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            raise ValueError(f"unsupported encrypt_padding: {encrypt_padding}")

        def _build_decrypt_pad() -> padding.AsymmetricPadding:
            pad_norm = str(decrypt_padding or "oaep").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)
            if pad_norm == "oaep":
                return padding.OAEP(mgf=padding.MGF1(algorithm=mgf_algo_obj), algorithm=algo_obj, label=None if label is None else _as_bytes(label))
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
                return padding.PSS(mgf=padding.MGF1(mgf_algo_obj), salt_length=_resolve_sign_salt_length(salt_length, algo_obj))
            raise ValueError(f"unsupported sign_padding: {sign_padding}")

        def _build_verify_pad() -> padding.AsymmetricPadding:
            pad_norm = str(verify_padding or "pkcs1v15").strip().lower()
            algo_obj = _resolve_hash(algorithm)
            mgf_algo_obj = _resolve_hash(mgf_algorithm or algorithm)
            if pad_norm == "pkcs1v15":
                return padding.PKCS1v15()
            if pad_norm == "pss":
                return padding.PSS(mgf=padding.MGF1(mgf_algo_obj), salt_length=_resolve_verify_salt_length(salt_length, algo_obj))
            raise ValueError(f"unsupported verify_padding: {verify_padding}")

        if tp == "rsa_sign":
            if not private_key:
                raise ValueError("private_key is required")
            signature_bytes = _load_rsa_private_key(private_key).sign(_as_bytes(input_value), _build_sign_pad(), _resolve_hash(algorithm))
            value: typing.Any = _encode_result(signature_bytes, out_mode)

        elif tp == "rsa_verify":
            if not public_key:
                raise ValueError("public_key is required")
            if signature is None:
                raise ValueError("signature is required")

            try:
                _load_rsa_public_key(public_key).verify(
                    _decode_result(signature, signature_format),
                    _as_bytes(input_value),
                    _build_verify_pad(),
                    _resolve_hash(algorithm),
                )
                value = True
            except InvalidSignature:
                value = False

        elif tp == "rsa_encrypt":
            if not public_key:
                raise ValueError("public_key is required")
            encrypted_bytes = _load_rsa_public_key(public_key).encrypt(_as_bytes(input_value), _build_encrypt_pad())
            value = _encode_result(encrypted_bytes, out_mode)

        elif tp == "rsa_decrypt":
            if not private_key:
                raise ValueError("private_key is required")
            if ciphertext is None:
                raise ValueError("ciphertext is required")
            plaintext_bytes = _load_rsa_private_key(private_key).decrypt(_decode_result(ciphertext, ciphertext_format), _build_decrypt_pad())
            try:
                value = plaintext_bytes.decode(encoding)
            except UnicodeDecodeError:
                value = plaintext_bytes

        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": display_kind, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def aes(
        *,
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
    ) -> dict[str, typing.Any]:
        """执行 AES 加密或解密，支持 CBC / ECB / GCM。"""

        tp     = str(kind or "").strip().lower()
        out    = str(output or "").strip()
        mode_v = str(mode or "cbc").strip().lower()
        pad_v  = str(padding_mode or "pkcs7").strip().lower()
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
            if fmt_v in {"bytes", "text"}:
                return _to_bytes(v)
            if fmt_v == "base64":
                return base64.b64decode(_to_text(v).strip(), validate=True)
            if fmt_v == "hex":
                return bytes.fromhex(_to_text(v).strip())
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
                return Cipher(algorithms.AES(key_content), modes.GCM(nonce_bytes, tag_content) if tag_content is not None else modes.GCM(nonce_bytes))
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
            encryptor = _build_cipher(key_bytes).encryptor()
            if mode_v == "gcm":
                aad_bytes = _decode(aad, aad_format) if aad is not None else b""
                if aad_bytes:
                    encryptor.authenticate_additional_data(aad_bytes)
                cipher_bytes = encryptor.update(plain_bytes) + encryptor.finalize()
                value: typing.Any = {"ciphertext": _encode(cipher_bytes, out_mode), "tag": _encode(encryptor.tag, out_mode)}
            else:
                cipher_bytes = encryptor.update(_pad_plain(plain_bytes)) + encryptor.finalize()
                value = _encode(cipher_bytes, out_mode)

        elif tp == "aes_decrypt":
            cipher = _build_cipher(key_bytes, _decode(tag, tag_format) if mode_v == "gcm" else None)
            if mode_v == "gcm" and tag is None:
                raise ValueError("tag is required for gcm decrypt")
            decryptor = cipher.decryptor()
            if mode_v == "gcm":
                aad_bytes = _decode(aad, aad_format) if aad is not None else b""
                if aad_bytes:
                    decryptor.authenticate_additional_data(aad_bytes)
                plain_bytes = decryptor.update(_decode(input_value, input_format)) + decryptor.finalize()
            else:
                plain_bytes = _unpad_plain(decryptor.update(_decode(input_value, input_format)) + decryptor.finalize())
            value = _encode(plain_bytes, out_mode)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def sign_text(
        *,
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
    ) -> dict[str, typing.Any]:
        """按稳定规则拼装签名文本，供上层继续做摘要或签名。"""

        tp  = str(kind or "").strip().lower()
        out = str(output or "").strip()
        if not out:
            raise ValueError("output is required")
        ignore_key_set = {str(x) for x in (ignore_keys or [])}

        def _is_empty(v: typing.Any) -> bool:
            return v in (None, "", [], {}, ())

        def _normalize_pairs(obj: dict[str, typing.Any]) -> list[tuple[str, str]]:
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

        if tp == "key_value_join":
            if not isinstance(data, dict):
                raise ValueError("data must be dict")
            value: typing.Any = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in _normalize_pairs(data))

        elif tp == "query_like":
            if not isinstance(data, dict):
                raise ValueError("data must be dict")
            value = pair_sep.join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in _normalize_pairs(data))

        elif tp == "body_plus_secret":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            body = "".join("" if x is None else str(x) for x in items)
            value = f"{prefix}{body}{suffix}{'' if secret is None else str(secret)}"

        elif tp == "prefix_suffix":
            if not isinstance(items, list):
                raise ValueError("items must be list")
            body = "".join("" if x is None else str(x) for x in items)
            value = f"{prefix}{body}{suffix}"

        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}

    @staticmethod
    def multipart_sign(
        *,
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
    ) -> dict[str, typing.Any]:
        """为 multipart 上传场景构造稳定可签名的文本表示。"""

        tp  = str(kind or "").strip().lower()
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
                file_text = filename if use_filename_only else ("" if value is None else str(value))
                if include_content_type:
                    file_text = f"{file_text}:{content_type}"
                if ignore_empty and _is_empty(file_text):
                    continue
                pairs.append((field_name, file_text))
            if sort_keys:
                pairs.sort(key=lambda x: (x[0], x[1]))
            return pairs

        field_pairs = _normalize_field_pairs(fields or {})
        file_pairs  = _normalize_file_pairs(files or [])

        if tp == "multipart_field_text":
            value: typing.Any = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in field_pairs)

        elif tp == "multipart_file_text":
            value = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in file_pairs)

        elif tp == "multipart_all_text":
            all_pairs = field_pairs + file_pairs
            if sort_keys:
                all_pairs.sort(key=lambda x: (x[0], x[1]))
            value = pair_sep.join(f"{k}{kv_sep}{v}" for k, v in all_pairs)

        else:
            raise ValueError(f"unsupported kind: {kind}")

        return {"kind": tp, "output": out, "value": value, "data": {out: value}}


if __name__ == '__main__':
    pass
