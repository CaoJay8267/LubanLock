"""Deterministic packaging and verification.

A package is either a ZIP file or a directory containing::

    manifest.json
    agent.json
    assets/<original relative paths>

ZIP output is reproducible: entries are sorted, timestamps are fixed and the
compression settings are pinned.  Verification re-derives the fingerprint and
checks every checksum, so a package can be validated without trusting the
producer.
"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from dotagent._version import __version__
from dotagent.build.compiler import CompiledAgent, compute_fingerprint
from dotagent.build.signing import (
    SIGNATURE_NAME,
    TIMESTAMP_NAME,
    request_timestamp,
    sign_manifest,
    verify_manifest,
    verify_timestamp,
)
from dotagent.core.errors import PackageError
from dotagent.core.utils import pretty_json, sha256_bytes

PACKAGE_FORMAT = "dotagent-package/1"
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 10_000
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class PackageVerification:
    ok: bool
    path: str
    fingerprint: str | None = None
    manifest: dict | None = None
    agent: dict | None = None
    errors: tuple[str, ...] = ()
    signed_by: str | None = None
    signature: dict | None = None

    def to_dict(self) -> dict:
        data = {
            "ok": self.ok,
            "path": self.path,
            "fingerprint": self.fingerprint,
            "errors": list(self.errors),
        }
        if self.agent:
            data["agent"] = {
                "name": self.agent.get("name"),
                "version": self.agent.get("version"),
            }
        if self.signature:
            data["signature"] = {
                "signedBy": self.signed_by,
                "alg": self.signature.get("alg"),
            }
        return data


def package_agent(
    compiled: CompiledAgent,
    out: str | Path,
    *,
    fmt: str = "zip",
    sign_key: str | Path | None = None,
    sign_cert: str | Path | None = None,
    sign_chain: tuple[str | Path, ...] = (),
    timestamp_url: str | None = None,
    timestamp_timeout: float = 30.0,
) -> Path:
    """Write *compiled* as a reproducible package and return its path."""

    files = _package_files(compiled)
    manifest = {
        "format": PACKAGE_FORMAT,
        "apiVersion": compiled.api_version,
        "createdBy": {"name": "dotagent", "version": __version__},
        "fingerprint": compiled.fingerprint,
        "agent": {
            "name": compiled.definition.metadata.name,
            "version": compiled.definition.metadata.version,
            "description": compiled.definition.metadata.description,
        },
        "entries": {
            name: {"sha256": sha256_bytes(data), "size": len(data)}
            for name, data in sorted(files.items())
        },
    }
    manifest_bytes = pretty_json(manifest).encode("utf-8")
    files["manifest.json"] = manifest_bytes
    if sign_key is not None:
        files[SIGNATURE_NAME] = pretty_json(
            sign_manifest(
                manifest_bytes,
                sign_key,
                certificate_path=sign_cert,
                chain_paths=sign_chain,
            )
        ).encode("utf-8")
    if timestamp_url is not None:
        files[TIMESTAMP_NAME] = request_timestamp(
            manifest_bytes, timestamp_url, timeout=timestamp_timeout
        )
    destination = Path(out)
    if fmt == "zip":
        _write_zip(destination, files)
        return destination
    if fmt == "dir":
        _write_directory(destination, files)
        return destination
    raise PackageError(f"unsupported package format {fmt!r}; use 'zip' or 'dir'")


def _package_files(compiled: CompiledAgent) -> dict[str, bytes]:
    files = {"agent.json": compiled.to_json_text().encode("utf-8")}
    for asset in compiled.assets:
        files[f"assets/{asset.path}"] = asset.data
    return files


def _write_zip(destination: Path, files: dict[str, bytes]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    os.replace(temporary, destination)


def _write_directory(destination: Path, files: dict[str, bytes]) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise PackageError(
            f"directory package destination is not empty: {destination}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def verify_package(
    path: str | Path,
    *,
    public_key: str | Path | None = None,
    ca_cert: str | Path | tuple[str | Path, ...] | None = None,
    crl: str | Path | tuple[str | Path, ...] | None = None,
    fetch_crl: bool = False,
    ocsp: bool = False,
    ocsp_url: str | None = None,
    timeout: float = 10.0,
    offline: bool = False,
    cache_max_age: float = 86_400,
    proxy: str | None = None,
    timestamp_cert: str | Path | None = None,
    require_timestamp: bool = False,
    require_signature: bool = False,
) -> PackageVerification:
    """Check the structure, checksums, fingerprint and optional signature."""

    location = Path(path)
    errors: list[str] = []
    try:
        files = _read_package(location)
    except PackageError as exc:
        return PackageVerification(ok=False, path=str(location), errors=(str(exc),))

    manifest = _parse_manifest(files.get("manifest.json"), errors)
    agent_document = _parse_agent(files.get("agent.json"), errors)
    signature = _parse_signature(files.get(SIGNATURE_NAME), errors)
    timestamp = files.get(TIMESTAMP_NAME)
    fingerprint: str | None = None

    if manifest is not None:
        if manifest.get("format") != PACKAGE_FORMAT:
            errors.append(
                f"unsupported package format {manifest.get('format')!r}; "
                f"expected {PACKAGE_FORMAT!r}"
            )
        entries = manifest.get("entries")
        if not isinstance(entries, dict):
            errors.append("manifest.entries must be an object")
        else:
            _verify_entries(files, entries, errors)
        fingerprint = manifest.get("fingerprint")

    if signature is None and require_signature:
        errors.append("package is not signed (signature.json is missing)")
    if signature is None and (public_key is not None or ca_cert):
        errors.append("package is not signed but a verification key was provided")
    if signature is not None and require_signature and public_key is None and not ca_cert:
        errors.append("--public-key or --ca-cert is required to verify a signed package")
    if signature is not None:
        anchors: tuple[str | Path, ...] = ()
        if ca_cert is not None:
            anchors = ca_cert if isinstance(ca_cert, tuple) else (ca_cert,)
        crls: tuple[str | Path, ...] = ()
        if crl is not None:
            crls = crl if isinstance(crl, tuple) else (crl,)
        if crls and not signature.get("certificate"):
            errors.append("--crl requires a certificate signature")
        elif not signature.get("certificate") and anchors and public_key is None:
            errors.append("raw signature requires --public-key (not --ca-cert)")
        elif signature.get("certificate"):
            if anchors:
                try:
                    verify_manifest(
                        files["manifest.json"],
                        signature,
                        public_key_path=public_key,
                        ca_cert_paths=anchors,
                        crl_paths=crls,
                        fetch_crl=fetch_crl,
                        ocsp=ocsp,
                        ocsp_url=ocsp_url,
                        timeout=timeout,
                        offline=offline,
                        cache_max_age=cache_max_age,
                        proxy=proxy,
                    )
                except (PackageError, KeyError) as exc:
                    errors.append(str(exc))
            elif crls:
                errors.append("--crl requires --ca-cert")
            elif ocsp:
                errors.append("--ocsp requires --ca-cert")
        elif public_key is not None:
            try:
                verify_manifest(
                    files["manifest.json"],
                    signature,
                    public_key_path=public_key,
                )
            except (PackageError, KeyError) as exc:
                errors.append(str(exc))

    if agent_document is not None:
        agent = agent_document.get("agent")
        assets = agent_document.get("assets")
        if not isinstance(agent, dict) or not isinstance(assets, dict):
            errors.append("agent.json is missing the 'agent' or 'assets' object")
        else:
            recomputed = compute_fingerprint(agent, assets)
            fingerprint = fingerprint or agent_document.get("fingerprint")
            if agent_document.get("fingerprint") != recomputed:
                errors.append(
                    "agent.json fingerprint is inconsistent with its content"
                )
            if manifest is not None and manifest.get("fingerprint") != recomputed:
                errors.append("manifest fingerprint does not match agent.json")
            _verify_assets(files, assets, errors)

    if require_timestamp and timestamp is None:
        errors.append("package has no RFC 3161 timestamp")
    elif timestamp is not None and manifest is not None:
        try:
            verify_timestamp(
                files["manifest.json"],
                timestamp,
                tsa_cert=timestamp_cert,
            )
        except PackageError as exc:
            errors.append(str(exc))

    return PackageVerification(
        ok=not errors,
        path=str(location),
        fingerprint=fingerprint,
        manifest=manifest,
        agent=agent_document.get("agent") if agent_document else None,
        errors=tuple(errors),
        signed_by=signature.get("keyId") if signature else None,
        signature=signature,
    )


def _parse_manifest(data: bytes | None, errors: list[str]) -> dict | None:
    if data is None:
        errors.append("package has no manifest.json")
        return None
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"manifest.json is invalid JSON: {exc}")
        return None
    if not isinstance(manifest, dict):
        errors.append("manifest.json must be a JSON object")
        return None
    return manifest


def _parse_agent(data: bytes | None, errors: list[str]) -> dict | None:
    if data is None:
        errors.append("package has no agent.json")
        return None
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"agent.json is invalid JSON: {exc}")
        return None
    if not isinstance(document, dict):
        errors.append("agent.json must be a JSON object")
        return None
    return document


def _parse_signature(data: bytes | None, errors: list[str]) -> dict | None:
    if data is None:
        return None
    try:
        signature = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"signature.json is invalid JSON: {exc}")
        return None
    if not isinstance(signature, dict):
        errors.append("signature.json must be a JSON object")
        return None
    return signature


def _verify_entries(
    files: dict[str, bytes], entries: dict, errors: list[str]
) -> None:
    actual = {
        name for name in files if name not in {"manifest.json", SIGNATURE_NAME, TIMESTAMP_NAME}
    }
    for name, meta in entries.items():
        if not isinstance(meta, dict):
            errors.append(f"manifest entry {name!r} must be an object")
            continue
        if name not in actual:
            errors.append(f"manifest declares missing entry {name!r}")
            continue
        data = files[name]
        if meta.get("size") != len(data):
            errors.append(f"size mismatch for {name!r}")
        if meta.get("sha256") != sha256_bytes(data):
            errors.append(f"checksum mismatch for {name!r}")
    for name in sorted(actual - set(entries)):
        errors.append(f"unlisted entry {name!r}")


def _verify_assets(files: dict[str, bytes], assets: dict, errors: list[str]) -> None:
    seen: set[str] = set()
    for relative, meta in assets.items():
        if not isinstance(meta, dict):
            errors.append(f"asset {relative!r} metadata must be an object")
            continue
        name = f"assets/{relative}"
        seen.add(name)
        data = files.get(name)
        if data is None:
            errors.append(f"missing asset {relative!r}")
            continue
        if meta.get("sha256") != sha256_bytes(data):
            errors.append(f"asset checksum mismatch for {relative!r}")
        if meta.get("size") != len(data):
            errors.append(f"asset size mismatch for {relative!r}")
    for name in files:
        if name.startswith("assets/") and name not in seen:
            errors.append(f"unexpected asset entry {name!r}")


def read_package_files(path: str | Path) -> dict[str, bytes]:
    """Public helper used by importers; returns path -> bytes."""

    return _read_package(Path(path))


def _read_package(path: Path) -> dict[str, bytes]:
    if path.is_dir():
        files: dict[str, bytes] = {}
        total = 0
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            relative = child.relative_to(path).as_posix()
            data = child.read_bytes()
            total += len(data)
            if total > MAX_PACKAGE_BYTES:
                raise PackageError("package exceeds the size limit")
            files[relative] = data
        return files
    if not path.is_file():
        raise PackageError(f"package does not exist: {path}")
    if path.suffix.lower() not in {".zip"}:
        raise PackageError(
            f"unsupported package file {path.name!r}; expected .zip or a directory"
        )
    files = {}
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise PackageError("package has too many entries")
            for info in infos:
                if info.is_dir():
                    continue
                name = info.filename
                pure = PurePosixPath(name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or name.startswith("\\")
                    or ":" in name
                ):
                    raise PackageError(f"unsafe package entry: {name!r}")
                data = archive.read(info)
                total += len(data)
                if total > MAX_PACKAGE_BYTES:
                    raise PackageError("package exceeds the size limit")
                files[name] = data
    except zipfile.BadZipFile as exc:
        raise PackageError(f"not a valid ZIP archive: {path}") from exc
    return files
