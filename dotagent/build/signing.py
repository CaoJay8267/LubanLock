"""Optional Ed25519 package signing, including X.509 certificate chains.

Two modes are supported:

* raw Ed25519 key pair (``--sign-key`` / ``--public-key``),
* certificate mode (``--sign-key`` + ``--sign-cert`` / ``--ca-cert``), where the
  signature carries the leaf certificate and an optional intermediate chain.
  The trust anchor is supplied at verification time, so certificate rotation
  works: old and new leaves verify against the same root CA.

Certificates are Ed25519-only; signing remains optional and unsigned packages
stay reproducible.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotagent.core.errors import PackageError
from dotagent.core.utils import sha256_bytes

SIGNATURE_NAME = "signature.json"
SIGNATURE_FORMAT = "dotagent-signature/1"
ALGORITHM = "ed25519"


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.x509.oid import NameOID
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PackageError(
            "package signing requires the optional 'cryptography' dependency; "
            "install it with: pip install 'dotagent[signing]'"
        ) from exc
    return {
        "x509": x509,
        "serialization": serialization,
        "Ed25519PrivateKey": Ed25519PrivateKey,
        "Ed25519PublicKey": Ed25519PublicKey,
        "NameOID": NameOID,
    }


def generate_keypair(directory: str | Path) -> tuple[Path, Path]:
    """Create a raw Ed25519 keypair and return ``(private.pem, public.pem)``."""

    crypto = _crypto()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    private_key = crypto["Ed25519PrivateKey"].generate()
    private_path = target / "private.pem"
    public_path = target / "public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=crypto["serialization"].Encoding.PEM,
            format=crypto["serialization"].PrivateFormat.PKCS8,
            encryption_algorithm=crypto["serialization"].NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=crypto["serialization"].Encoding.PEM,
            format=crypto["serialization"].PublicFormat.SubjectPublicKeyInfo,
        )
    )
    _restrict(private_path)
    return private_path, public_path


def generate_certificate_chain(
    directory: str | Path,
    *,
    common_name: str = "DotAgent",
    root_days: int = 3650,
    leaf_days: int = 730,
) -> tuple[Path, Path, Path]:
    """Create a root CA + signing certificate.

    Returns ``(signing_key, signing_cert, root_ca_cert)``.  The root private key
    is written as ``root-key.pem`` so certificates can be rotated.
    """

    crypto = _crypto()
    x509 = crypto["x509"]
    serialization = crypto["serialization"]
    Ed25519PrivateKey = crypto["Ed25519PrivateKey"]
    NameOID = crypto["NameOID"]
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    root_key = Ed25519PrivateKey.generate()
    root_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DotAgent"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{common_name} Root CA"),
        ]
    )
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=root_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, None)
    )
    leaf_key = Ed25519PrivateKey.generate()
    leaf_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DotAgent"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{common_name} Signing"),
        ]
    )
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=leaf_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, None)
    )
    files = {
        "root-key.pem": root_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        "root-ca.pem": root_cert.public_bytes(serialization.Encoding.PEM),
        "signing-key.pem": leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        "signing.pem": leaf_cert.public_bytes(serialization.Encoding.PEM),
    }
    written: dict[str, Path] = {}
    for name, data in files.items():
        path = target / name
        path.write_bytes(data)
        written[name] = path
    _restrict(written["root-key.pem"])
    _restrict(written["signing-key.pem"])
    private_key = written["signing-key.pem"]
    signing_cert = written["signing.pem"]
    root_cert_path = written["root-ca.pem"]
    return private_key, signing_cert, root_cert_path


def rotate_signing_certificate(
    directory: str | Path,
    *,
    root_key_path: str | Path,
    root_cert_path: str | Path,
    common_name: str = "DotAgent",
    leaf_days: int = 730,
) -> tuple[Path, Path]:
    """Issue a new leaf certificate under an existing root CA.

    Old and new packages verify against the same ``root-ca.pem``, which is the
    key-rotation path.
    """

    crypto = _crypto()
    x509 = crypto["x509"]
    serialization = crypto["serialization"]
    Ed25519PrivateKey = crypto["Ed25519PrivateKey"]
    NameOID = crypto["NameOID"]
    root_key = serialization.load_pem_private_key(
        Path(root_key_path).read_bytes(), password=None
    )
    if not isinstance(root_key, Ed25519PrivateKey):
        raise PackageError("rotation requires an Ed25519 root CA key")
    root_cert = _load_certificate(root_cert_path)
    if _public_bytes(root_cert.public_key()) != _public_bytes(root_key.public_key()):
        raise PackageError("root CA certificate does not match the root key")
    now = datetime.now(UTC)
    leaf_key = Ed25519PrivateKey.generate()
    leaf_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DotAgent"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{common_name} Signing"),
        ]
    )
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=leaf_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, None)
    )
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    key_path = target / "signing-key.pem"
    cert_path = target / "signing.pem"
    key_path.write_bytes(
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    _restrict(key_path)
    return key_path, cert_path


def _restrict(path: Path) -> None:
    try:  # best effort on platforms that support POSIX permissions
        path.chmod(0o600)
    except OSError:  # pragma: no cover
        pass


def _public_key_id(public_key) -> str:
    serialization = _crypto()["serialization"]
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sha256_bytes(raw)[:16]


def sign_manifest(
    manifest_bytes: bytes,
    private_key_path: str | Path,
    *,
    certificate_path: str | Path | None = None,
    chain_paths: tuple[str | Path, ...] = (),
) -> dict:
    crypto = _crypto()
    serialization = crypto["serialization"]
    private_key = serialization.load_pem_private_key(
        Path(private_key_path).read_bytes(), password=None
    )
    if not isinstance(private_key, crypto["Ed25519PrivateKey"]):
        raise PackageError("signing requires an Ed25519 private key")
    signature = private_key.sign(manifest_bytes)
    document: dict = {
        "format": SIGNATURE_FORMAT,
        "alg": ALGORITHM,
        "keyId": _public_key_id(private_key.public_key()),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    if certificate_path is not None:
        certificate = _load_certificate(certificate_path)
        if _public_bytes(certificate.public_key()) != _public_bytes(
            private_key.public_key()
        ):
            raise PackageError("signing certificate does not match the private key")
        document["certificate"] = Path(certificate_path).read_text(encoding="utf-8")
        document["chain"] = [
            Path(path).read_text(encoding="utf-8") for path in chain_paths
        ]
        document["keyId"] = _public_key_id(certificate.public_key())
    return document


def verify_manifest(
    manifest_bytes: bytes,
    signature: dict,
    *,
    public_key_path: str | Path | None = None,
    ca_cert_paths: tuple[str | Path, ...] = (),
) -> None:
    """Raise :class:`PackageError` when the signature does not verify."""

    crypto = _crypto()
    serialization = crypto["serialization"]
    if signature.get("format") != SIGNATURE_FORMAT:
        raise PackageError(f"unsupported signature format {signature.get('format')!r}")
    if signature.get("alg") != ALGORITHM:
        raise PackageError(f"unsupported signature algorithm {signature.get('alg')!r}")
    try:
        signature_bytes = base64.b64decode(signature["signature"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise PackageError(f"malformed signature payload: {exc}") from exc

    certificate_pem = signature.get("certificate")
    if certificate_pem:
        if not ca_cert_paths:
            raise PackageError("certificate signature requires a --ca-cert trust anchor")
        leaf = _load_certificate_pem(certificate_pem)
        intermediates = [
            _load_certificate_pem(pem) for pem in signature.get("chain") or ()
        ]
        anchors: list = []
        for path in ca_cert_paths:
            anchors.extend(_load_certificates(path))
        _validate_chain(leaf, intermediates, anchors)
        public_key = leaf.public_key()
    else:
        if public_key_path is None:
            raise PackageError("signature requires --public-key")
        public_key = serialization.load_pem_public_key(
            Path(public_key_path).read_bytes()
        )
        if not isinstance(public_key, crypto["Ed25519PublicKey"]):
            raise PackageError("verification requires an Ed25519 public key")
    try:
        public_key.verify(signature_bytes, manifest_bytes)
    except Exception as exc:
        raise PackageError("signature verification failed") from exc
    expected_key = _public_key_id(public_key)
    if signature.get("keyId") and signature["keyId"] != expected_key:
        raise PackageError(
            f"signature key id {signature['keyId']!r} does not match the key "
            f"({expected_key!r})"
        )


def _load_certificate(path: str | Path):
    crypto = _crypto()
    try:
        return crypto["x509"].load_pem_x509_certificate(Path(path).read_bytes())
    except ValueError as exc:
        raise PackageError(f"cannot load certificate {path}: {exc}") from exc


def _load_certificate_pem(pem: str):
    crypto = _crypto()
    try:
        return crypto["x509"].load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError as exc:
        raise PackageError(f"cannot load certificate in signature: {exc}") from exc


def _load_certificates(path: str | Path) -> list:
    crypto = _crypto()
    try:
        return crypto["x509"].load_pem_x509_certificates(Path(path).read_bytes())
    except ValueError as exc:
        raise PackageError(f"cannot load certificates {path}: {exc}") from exc


def _public_bytes(public_key) -> bytes:
    serialization = _crypto()["serialization"]
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _validate_chain(leaf, intermediates: list, anchors: list) -> None:
    crypto = _crypto()
    x509 = crypto["x509"]
    now = datetime.now(UTC)
    chain = [leaf, *intermediates]
    for index, certificate in enumerate(chain):
        not_before = getattr(certificate, "not_valid_before_utc", None)
        not_after = getattr(certificate, "not_valid_after_utc", None)
        if not_before is None:  # pragma: no cover - older cryptography
            not_before = certificate.not_valid_before.replace(tzinfo=UTC)
            not_after = certificate.not_valid_after.replace(tzinfo=UTC)
        assert not_before is not None and not_after is not None
        if not (not_before <= now <= not_after):
            raise PackageError(f"certificate is not valid now (chain index {index})")
    if any(
        isinstance(extension.value, x509.BasicConstraints) and extension.value.ca
        for extension in leaf.extensions
    ):
        raise PackageError("leaf certificate must not be a CA")
    for index in range(len(chain) - 1):
        _check_issued_by(chain[index], chain[index + 1])
    for certificate in chain[1:]:
        _require_ca(certificate)
    last = chain[-1]
    for anchor in anchors:
        if anchor.subject == last.issuer:
            _check_issued_by(last, anchor)
            _require_ca(anchor)
            return
    raise PackageError("certificate chain does not reach a supplied trust anchor")


def _require_ca(certificate) -> None:
    crypto = _crypto()
    x509 = crypto["x509"]
    if not any(
        isinstance(extension.value, x509.BasicConstraints) and extension.value.ca
        for extension in certificate.extensions
    ):
        raise PackageError("certificate chain contains a non-CA issuer")
    if not any(
        isinstance(extension.value, x509.KeyUsage) and extension.value.key_cert_sign
        for extension in certificate.extensions
    ):
        raise PackageError("CA certificate lacks keyCertSign usage")


def _check_issued_by(certificate, issuer) -> None:
    if certificate.issuer != issuer.subject:
        raise PackageError("certificate issuer does not match chain subject")
    crypto = _crypto()
    public_key = issuer.public_key()
    if not isinstance(public_key, crypto["Ed25519PublicKey"]):
        raise PackageError("only Ed25519 certificate chains are supported")
    try:
        public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
    except Exception as exc:
        raise PackageError("certificate signature verification failed") from exc


__all__ = [
    "ALGORITHM",
    "SIGNATURE_FORMAT",
    "SIGNATURE_NAME",
    "generate_certificate_chain",
    "generate_keypair",
    "rotate_signing_certificate",
    "sign_manifest",
    "verify_manifest",
]
