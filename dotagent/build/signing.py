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
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotagent.core.errors import PackageError
from dotagent.core.utils import sha256_bytes

SIGNATURE_NAME = "signature.json"
TIMESTAMP_NAME = "timestamp.tst"
SIGNATURE_FORMAT = "dotagent-signature/1"
ALGORITHM = "ed25519"
MAX_CRL_BYTES = 8 * 1024 * 1024
MAX_OCSP_BYTES = 2 * 1024 * 1024
MAX_TIMESTAMP_BYTES = 8 * 1024 * 1024
DEFAULT_CACHE_MAX_AGE = 86_400


def _cache_root() -> Path:
    configured = os.environ.get("DOTAGENT_CACHE_DIR")
    if configured:
        return Path(configured) / "pki"
    return Path.home() / ".cache" / "dotagent" / "pki"


def _cached_bytes(
    url: str,
    *,
    kind: str,
    timeout: float,
    max_age: float = DEFAULT_CACHE_MAX_AGE,
    offline: bool = False,
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    max_bytes: int,
) -> bytes:
    key = sha256_bytes(url.encode("utf-8"))[:32]
    path = _cache_root() / f"{kind}-{key}.bin"
    if offline:
        if path.is_file():
            return path.read_bytes()
        raise PackageError(f"offline cache miss for {url}")
    if path.is_file() and datetime.now(UTC).timestamp() - path.stat().st_mtime <= max_age:
        return path.read_bytes()
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method="POST" if data is not None else "GET"
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    ) if proxy else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
    except urllib.error.URLError as exc:
        raise PackageError(f"cannot fetch {url}: {exc.reason}") from exc
    if len(payload) > max_bytes:
        raise PackageError(f"{url} response exceeds the size limit")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        path.write_bytes(payload)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass
    return payload


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.x509 import ocsp
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
        "hashes": hashes,
        "ocsp": ocsp,
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
    crl_url: str | None = None,
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
    )
    if crl_url:
        leaf_cert = leaf_cert.add_extension(
            x509.CRLDistributionPoints(
                [
                    x509.DistributionPoint(
                        full_name=[x509.UniformResourceIdentifier(crl_url)],
                        relative_name=None,
                        reasons=None,
                        crl_issuer=None,
                    )
                ]
            ),
            critical=False,
        )
    leaf_cert = leaf_cert.sign(root_key, None)
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
    crl_paths: tuple[str | Path, ...] = (),
    fetch_crl: bool = False,
    ocsp: bool = False,
    ocsp_url: str | None = None,
    timeout: float = 10.0,
    offline: bool = False,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    proxy: str | None = None,
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
        _validate_chain(
            leaf,
            intermediates,
            anchors,
            crl_paths=crl_paths,
            fetch_crl=fetch_crl,
            ocsp=ocsp,
            ocsp_url=ocsp_url,
            timeout=timeout,
            offline=offline,
            cache_max_age=cache_max_age,
            proxy=proxy,
        )
        public_key = leaf.public_key()
    else:
        if crl_paths or fetch_crl or ocsp:
            raise PackageError("CRL/OCSP checks require a certificate signature")
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


def _check_revocations(
    chain: list,
    anchors: list,
    crl_paths,
    *,
    fetch: bool = False,
    timeout: float = 10.0,
    offline: bool = False,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    proxy: str | None = None,
) -> None:
    now = datetime.now(UTC)
    all_certificates = [*chain, *anchors]
    crls = []
    for path in crl_paths:
        data = Path(path).read_bytes()
        crls.append(_parse_crl(data, str(path)))
    if fetch:
        crls.extend(
            _fetch_crls(
                chain[0],
                timeout=timeout,
                offline=offline,
                cache_max_age=cache_max_age,
                proxy=proxy,
            )
        )
    leaf = chain[0]
    covered = False
    for crl in crls:
        issuer = next(
            (certificate for certificate in all_certificates if certificate.subject == crl.issuer),
            None,
        )
        if issuer is None:
            continue
        covered = covered or issuer.subject == leaf.issuer
        try:
            valid = crl.is_signature_valid(issuer.public_key())
        except AttributeError:  # pragma: no cover - older cryptography
            try:
                issuer.public_key().verify(crl.signature, crl.tbs_certlist_bytes)
                valid = True
            except Exception:  # noqa: BLE001 - cryptography fallback
                valid = False
        if not valid:
            raise PackageError(f"CRL issued by {issuer.subject.rfc4514_string()} has an invalid signature")
        last_update = getattr(crl, "last_update_utc", None)
        next_update = getattr(crl, "next_update_utc", None)
        if last_update is not None and now < last_update:
            raise PackageError("CRL is not valid yet")
        if next_update is not None and now > next_update:
            raise PackageError("CRL has expired")
        for certificate in chain:
            if certificate.issuer != crl.issuer:
                continue
            if crl.get_revoked_certificate_by_serial_number(certificate.serial_number):
                raise PackageError(
                    f"certificate serial {certificate.serial_number} has been revoked"
                )
    if not covered:
        raise PackageError("provided CRLs do not cover the signing certificate")


def _parse_crl(data: bytes, label: str):
    crypto = _crypto()
    x509 = crypto["x509"]
    try:
        return x509.load_pem_x509_crl(data)
    except ValueError:
        try:
            return x509.load_der_x509_crl(data)
        except ValueError as exc:
            raise PackageError(f"cannot load CRL {label}: {exc}") from exc


def _fetch_crls(
    certificate,
    *,
    timeout: float,
    offline: bool = False,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    proxy: str | None = None,
) -> list:
    crypto = _crypto()
    x509 = crypto["x509"]
    try:
        extension = certificate.extensions.get_extension_for_class(
            x509.CRLDistributionPoints
        )
    except x509.ExtensionNotFound as exc:
        raise PackageError("certificate has no CRL distribution point") from exc
    crls = []
    for point in extension.value:
        for name in point.full_name or ():
            url = getattr(name, "value", None)
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            data = _cached_bytes(
                url,
                kind="crl",
                timeout=timeout,
                max_age=cache_max_age,
                offline=offline,
                proxy=proxy,
                max_bytes=MAX_CRL_BYTES,
            )
            crls.append(_parse_crl(data, url))
    if not crls:
        raise PackageError("certificate CRL distribution points contain no HTTP URL")
    return crls


def _check_ocsp(
    leaf,
    chain: list,
    anchors: list,
    *,
    url: str | None,
    timeout: float,
    offline: bool = False,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    proxy: str | None = None,
) -> None:
    crypto = _crypto()
    x509 = crypto["x509"]
    ocsp = crypto["ocsp"]
    serialization = crypto["serialization"]
    issuer = next(
        (certificate for certificate in [*chain, *anchors] if certificate.subject == leaf.issuer),
        None,
    )
    if issuer is None:
        raise PackageError("cannot find the OCSP issuer certificate")
    if not url:
        try:
            extension = leaf.extensions.get_extension_for_class(
                x509.AuthorityInformationAccess
            )
        except x509.ExtensionNotFound as exc:
            raise PackageError("certificate has no OCSP responder URL") from exc
        for description in extension.value:
            if description.access_method == x509.AuthorityInformationAccessOID.OCSP:
                location = description.access_location
                url = getattr(location, "value", None)
                break
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise PackageError("certificate has no usable OCSP responder URL")
    request = (
        ocsp.OCSPRequestBuilder()
        .add_certificate(leaf, issuer, crypto["hashes"].SHA256())
        .build()
    )
    body = request.public_bytes(serialization.Encoding.DER)
    payload = _cached_bytes(
        url,
        kind="ocsp",
        timeout=timeout,
        max_age=cache_max_age,
        offline=offline,
        proxy=proxy,
        headers={"Content-Type": "application/ocsp-request"},
        data=body,
        max_bytes=MAX_OCSP_BYTES,
    )
    try:
        parsed = ocsp.load_der_ocsp_response(payload)
    except ValueError as exc:
        raise PackageError(f"cannot parse OCSP response: {exc}") from exc
    if parsed.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        raise PackageError(f"OCSP responder returned {parsed.response_status}")
    if parsed.serial_number != leaf.serial_number:
        raise PackageError("OCSP response is for a different certificate")
    if parsed.certificate_status == ocsp.OCSPCertStatus.REVOKED:
        raise PackageError("certificate has been revoked (OCSP)")
    if parsed.certificate_status != ocsp.OCSPCertStatus.GOOD:
        raise PackageError("OCSP status is unknown")
    now = datetime.now(UTC)
    this_update = getattr(parsed, "this_update_utc", None)
    next_update = getattr(parsed, "next_update_utc", None)
    if this_update is not None and now < this_update:
        raise PackageError("OCSP response is not valid yet")
    if next_update is not None and now > next_update:
        raise PackageError("OCSP response has expired")
    responder = next(
        (cert for cert in parsed.certificates if cert.subject == parsed.responder_name),
        issuer,
    )
    try:
        responder.public_key().verify(parsed.signature, parsed.tbs_response_bytes)
    except Exception as exc:
        raise PackageError("OCSP response signature verification failed") from exc


def _validate_chain(
    leaf,
    intermediates: list,
    anchors: list,
    crl_paths=(),
    *,
    fetch_crl: bool = False,
    ocsp: bool = False,
    ocsp_url: str | None = None,
    timeout: float = 10.0,
    offline: bool = False,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    proxy: str | None = None,
) -> None:
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
    if crl_paths or fetch_crl:
        _check_revocations(
            chain,
            anchors,
            crl_paths,
            fetch=fetch_crl,
            timeout=timeout,
            offline=offline,
            cache_max_age=cache_max_age,
            proxy=proxy,
        )
    if ocsp:
        _check_ocsp(
            leaf,
            chain,
            anchors,
            url=ocsp_url,
            timeout=timeout,
            offline=offline,
            cache_max_age=cache_max_age,
            proxy=proxy,
        )
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


def request_timestamp(
    data: bytes,
    tsa_url: str,
    *,
    timeout: float = 30.0,
    proxy: str | None = None,
) -> bytes:
    """Request an RFC 3161 timestamp token from *tsa_url*."""

    try:
        import rfc3161ng
        from pyasn1.codec.der import encoder
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise PackageError(
            "RFC 3161 timestamps require the optional 'rfc3161ng' dependency"
        ) from exc
    try:
        request = rfc3161ng.make_timestamp_request(
            data=data, hashname="sha256", include_tsa_certificate=True
        )
        payload = encoder.encode(request)
        opener = (
            urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
            if proxy
            else urllib.request.build_opener()
        )
        http_request = urllib.request.Request(
            tsa_url,
            data=payload,
            headers={"Content-Type": "application/timestamp-query"},
            method="POST",
        )
        with opener.open(http_request, timeout=timeout) as response:
            body = response.read(MAX_TIMESTAMP_BYTES + 1)
        if len(body) > MAX_TIMESTAMP_BYTES:
            raise PackageError("timestamp response exceeds the size limit")
        parsed = rfc3161ng.decode_timestamp_response(body)
        status = int(parsed["status"]["status"])
        if status != 0:
            raise PackageError(
                f"TSA rejected the request with status {parsed['status']['status']}"
            )
        token = parsed["timeStampToken"]
        if token is None:
            raise PackageError("TSA response has no timestamp token")
        return encoder.encode(token)
    except PackageError:
        raise
    except Exception as exc:
        raise PackageError(f"timestamp request failed: {exc}") from exc


def verify_timestamp(
    data: bytes,
    token: bytes,
    *,
    tsa_cert: str | Path | None = None,
) -> None:
    """Verify an RFC 3161 token over *data*, optionally against *tsa_cert*."""

    try:
        import rfc3161ng
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise PackageError(
            "RFC 3161 timestamps require the optional 'rfc3161ng' dependency"
        ) from exc
    certificate: bytes | None = None
    if tsa_cert is not None:
        certificate = Path(tsa_cert).read_bytes()
    try:
        if certificate is not None:
            rfc3161ng.check_timestamp(
                token,
                certificate=certificate,
                data=data,
                hashname="sha256",
            )
            return
        from pyasn1.codec.der import decoder, encoder

        tst, _ = decoder.decode(token, asn1Spec=rfc3161ng.TimeStampToken())
        errors: list[str] = []
        for choice in tst.content["certificates"]:
            try:
                rfc3161ng.check_timestamp(
                    token,
                    certificate=encoder.encode(choice),
                    data=data,
                    hashname="sha256",
                )
                return
            except Exception as exc:  # noqa: BLE001 - try the next chain cert
                errors.append(str(exc))
        raise PackageError(
            "timestamp verification failed for every embedded certificate: "
            + "; ".join(errors[-3:])
        )
    except PackageError:
        raise
    except Exception as exc:
        raise PackageError(f"timestamp verification failed: {exc}") from exc


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
