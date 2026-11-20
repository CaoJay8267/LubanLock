"""Optional Ed25519 package signing.

Signatures are detached metadata inside the package (``signature.json``) and
cover the exact bytes of ``manifest.json``.  Signing is optional: an unsigned
package remains reproducible; a signed package is deterministic for the same
key and content.

The cryptography dependency is optional (``pip install 'dotagent[signing]'``).
"""

from __future__ import annotations

import base64
from pathlib import Path

from dotagent.core.errors import PackageError
from dotagent.core.utils import sha256_bytes

SIGNATURE_NAME = "signature.json"
SIGNATURE_FORMAT = "dotagent-signature/1"
ALGORITHM = "ed25519"


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PackageError(
            "package signing requires the optional 'cryptography' dependency; "
            "install it with: pip install 'dotagent[signing]'"
        ) from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def generate_keypair(directory: str | Path) -> tuple[Path, Path]:
    """Create an Ed25519 keypair and return ``(private.pem, public.pem)``."""

    serialization, Ed25519PrivateKey, _ = _crypto()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_path = target / "private.pem"
    public_path = target / "public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    try:  # best effort on platforms that support POSIX permissions
        private_path.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    return private_path, public_path


def _public_key_id(public_key) -> str:
    serialization, _, _ = _crypto()
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sha256_bytes(raw)[:16]


def sign_manifest(manifest_bytes: bytes, private_key_path: str | Path) -> dict:
    serialization, _, _ = _crypto()
    key_bytes = Path(private_key_path).read_bytes()
    try:
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except (ValueError, TypeError) as exc:
        raise PackageError(f"cannot load private key {private_key_path}: {exc}") from exc
    if not hasattr(private_key, "sign"):
        raise PackageError("signing requires an Ed25519 private key")
    signature = private_key.sign(manifest_bytes)
    return {
        "format": SIGNATURE_FORMAT,
        "alg": ALGORITHM,
        "keyId": _public_key_id(private_key.public_key()),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest(
    manifest_bytes: bytes, signature: dict, public_key_path: str | Path
) -> None:
    """Raise :class:`PackageError` when the signature does not verify."""

    if signature.get("format") != SIGNATURE_FORMAT:
        raise PackageError(f"unsupported signature format {signature.get('format')!r}")
    if signature.get("alg") != ALGORITHM:
        raise PackageError(f"unsupported signature algorithm {signature.get('alg')!r}")
    serialization, _, Ed25519PublicKey = _crypto()
    key_bytes = Path(public_key_path).read_bytes()
    try:
        public_key = serialization.load_pem_public_key(key_bytes)
    except (ValueError, TypeError) as exc:
        raise PackageError(f"cannot load public key {public_key_path}: {exc}") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise PackageError("verification requires an Ed25519 public key")
    try:
        signature_bytes = base64.b64decode(signature["signature"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise PackageError(f"malformed signature payload: {exc}") from exc
    try:
        public_key.verify(signature_bytes, manifest_bytes)
    except Exception as exc:  # cryptography raises InvalidSignature
        raise PackageError("signature verification failed") from exc
    expected_key = _public_key_id(public_key)
    if signature.get("keyId") and signature["keyId"] != expected_key:
        raise PackageError(
            f"signature key id {signature['keyId']!r} does not match the key "
            f"({expected_key!r})"
        )


__all__ = [
    "ALGORITHM",
    "SIGNATURE_FORMAT",
    "SIGNATURE_NAME",
    "generate_keypair",
    "sign_manifest",
    "verify_manifest",
]
