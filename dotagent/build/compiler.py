"""Compilation from a source tree to a deterministic, portable artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotagent._version import __version__
from dotagent.core.errors import Diagnostic, LoadError, ValidationFailed
from dotagent.core.models import AgentDefinition
from dotagent.core.utils import (
    UnsafePathError,
    canonical_json,
    media_type_for,
    pretty_json,
    resolve_relative,
    sha256_bytes,
)
from dotagent.spec.loader import load_source
from dotagent.spec.validator import validate_source

API_VERSION = "dotagent/v1alpha1"
COMPILED_KIND = "CompiledAgent"
GENERATOR = {"name": "dotagent", "version": __version__}
MAX_ASSET_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Asset:
    path: str
    data: bytes
    media_type: str

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.data)

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class CompiledAgent:
    api_version: str
    definition: AgentDefinition
    ir: dict
    fingerprint: str
    assets: tuple[Asset, ...]
    warnings: tuple[Diagnostic, ...] = ()

    def asset(self, path: str) -> Asset | None:
        for asset in self.assets:
            if asset.path == path:
                return asset
        return None

    def asset_map(self) -> dict[str, Asset]:
        return {asset.path: asset for asset in self.assets}

    def to_json_text(self) -> str:
        return pretty_json(self.ir)


def compile_agent(path: str | Path, *, strict: bool = False) -> CompiledAgent:
    """Load, validate and compile an agent.

    Errors always raise.  ``strict=True`` additionally turns warnings into a
    failure, which is useful in CI.
    """

    source = load_source(path)
    result, definition = validate_source(source)
    if definition is None:
        result.raise_if_errors("agent validation failed")
        raise ValidationFailed(
            "agent could not be compiled", diagnostics=result.sorted()
        )
    if result.errors:
        raise ValidationFailed("agent validation failed", diagnostics=result.sorted())
    if strict and result.warnings:
        raise ValidationFailed(
            "agent validation produced warnings (strict mode)",
            diagnostics=result.sorted(),
        )
    return _assemble(definition, result)


def _assemble(definition: AgentDefinition, result) -> CompiledAgent:
    assets = _collect_assets(definition)
    asset_meta = {
        asset.path: {
            "sha256": asset.sha256,
            "size": asset.size,
            "mediaType": asset.media_type,
        }
        for asset in assets
    }
    agent_dict = definition.to_dict()
    fingerprint = compute_fingerprint(agent_dict, asset_meta)
    ir = {
        "apiVersion": API_VERSION,
        "kind": COMPILED_KIND,
        "generator": dict(GENERATOR),
        "fingerprint": fingerprint,
        "agent": agent_dict,
        "assets": asset_meta,
    }
    return CompiledAgent(
        api_version=API_VERSION,
        definition=definition,
        ir=ir,
        fingerprint=fingerprint,
        assets=assets,
        warnings=tuple(result.warnings),
    )


def compute_fingerprint(agent_dict: dict, asset_meta: dict) -> str:
    payload = {
        "apiVersion": API_VERSION,
        "kind": COMPILED_KIND,
        "agent": agent_dict,
        "assets": asset_meta,
    }
    return "sha256:" + sha256_bytes(canonical_json(payload).encode("utf-8"))


def _collect_assets(definition: AgentDefinition) -> tuple[Asset, ...]:
    paths: set[str] = set()
    if definition.instructions and definition.instructions.file:
        paths.add(definition.instructions.file)
    for item in definition.knowledge:
        paths.add(item.file)
    for skill in definition.skills:
        paths.add(skill.file)
        paths.update(skill.files)
    for subagent in definition.subagents:
        paths.add(subagent.file)
    for task in definition.tasks:
        if task.prompt and task.prompt.file:
            paths.add(task.prompt.file)

    collected: dict[str, bytes] = {}
    library_roots = dict(definition.library_roots)
    for relative in sorted(paths):
        base = definition.root if relative == "AGENTS.md" else definition.spec_dir
        source_relative = relative
        if relative.startswith("libraries/"):
            parts = Path(relative).parts
            if len(parts) >= 3 and parts[1] in library_roots:
                base = library_roots[parts[1]]
                source_relative = "/".join(parts[2:])
        try:
            path = resolve_relative(base, source_relative, field=f"asset {relative}")
        except UnsafePathError as exc:  # pragma: no cover - validated earlier
            raise LoadError(str(exc)) from exc
        if not path.is_file():
            raise LoadError(f"asset disappeared during compilation: {relative}")
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise LoadError(f"asset {relative} exceeds {MAX_ASSET_BYTES} bytes")
        data = path.read_bytes()
        collected[relative] = data
    return tuple(
        Asset(path=relative, data=data, media_type=media_type_for(relative))
        for relative, data in sorted(collected.items())
    )


def write_compiled(compiled: CompiledAgent, out: str | Path) -> Path:
    """Write ``agent.json`` and the asset tree into *out*."""

    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "agent.json").write_text(compiled.to_json_text(), encoding="utf-8")
    assets_root = directory / "assets"
    for asset in compiled.assets:
        destination = assets_root / Path(asset.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(asset.data)
    return directory


def read_compiled_json(path: str | Path) -> dict:
    import json

    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def compiled_from_directory(path: str | Path) -> dict:
    directory = Path(path)
    if directory.is_dir():
        directory = directory / "agent.json"
    if not directory.is_file():
        raise LoadError(f"compiled artifact not found: {directory}")
    return read_compiled_json(directory)


def check_compiled(compiled: CompiledAgent, out: str | Path) -> tuple[bool, str]:
    """Return whether the artifact in *out* matches *compiled*."""

    directory = Path(out)
    manifest = directory / "agent.json"
    if not manifest.is_file():
        return False, f"no compiled artifact at {manifest}"
    import json

    existing = json.loads(manifest.read_text(encoding="utf-8"))
    if existing.get("fingerprint") != compiled.fingerprint:
        return False, (
            "compiled artifact is stale: "
            f"expected {compiled.fingerprint}, found {existing.get('fingerprint')}"
        )
    for asset in compiled.assets:
        destination = directory / "assets" / Path(asset.path)
        if not destination.is_file():
            return False, f"missing asset {asset.path}"
        if destination.read_bytes() != asset.data:
            return False, f"asset {asset.path} differs"
    return True, "compiled artifact is up to date"


def read_asset_text(compiled: CompiledAgent, path: str) -> str:
    asset = compiled.asset(path)
    if asset is None:
        raise LoadError(f"asset not found: {path}")
    return asset.data.decode("utf-8")


__all__ = [
    "API_VERSION",
    "Asset",
    "CompiledAgent",
    "check_compiled",
    "compile_agent",
    "compiled_from_directory",
    "compute_fingerprint",
    "read_compiled_json",
    "write_compiled",
]
