"""Explicit cross-agent shared libraries (``spec.uses``).

A library is another DotAgent project.  It exports tools, models, skills and
knowledge.  Skill/knowledge assets are namespaced as ``libraries/<name>/...`` so
they never collide with local files.  Versions are checked with the small SemVer
helper, and the dependency list is explicit (no discovery).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dotagent.core.errors import Diagnostic
from dotagent.core.semver import matches, parse_version, valid_constraint
from dotagent.spec.inheritance import resolve_all_entities
from dotagent.spec.loader import AgentSource, load_source
from dotagent.spec.schema import validate_document

_IMPORTED_KINDS = ("tool", "model", "skill", "knowledge")


def attach_libraries(
    source: AgentSource, manifest: dict, diagnostics
) -> None:
    """Load libraries declared in ``spec.uses`` into *source*."""

    spec = manifest.get("spec")
    uses = spec.get("uses") if isinstance(spec, dict) else None
    if not isinstance(uses, list):
        return
    existing: dict[str, set[str]] = {
        "tool": set(),
        "model": set(),
        "skill": set(),
        "knowledge": set(),
    }
    # Seed with the main project's names so imports cannot shadow local entities.
    for kind in _IMPORTED_KINDS:
        for entity in source.entities.get(kind, ()):
            name = _entity_name(entity)
            if name:
                existing[kind].add(name)

    imported: dict[str, list] = {
        kind: list(source.entities.get(kind, ())) for kind in _IMPORTED_KINDS
    }
    library_roots: list[tuple[str, Path]] = list(source.library_roots)
    for index, use in enumerate(uses):
        if not isinstance(use, dict) or not isinstance(use.get("path"), str):
            continue
        relative = use["path"]
        constraint = use.get("version")
        library_root = (source.root / relative).resolve()
        if not library_root.is_dir():
            diagnostics.append(
                Diagnostic(
                    "DTA330",
                    f"shared library not found: {relative}",
                    path=source.manifest_rel,
                    pointer=f"/spec/uses/{index}/path",
                )
            )
            continue
        try:
            library = load_source(library_root)
        except Exception as exc:  # noqa: BLE001 - converted into a diagnostic
            diagnostics.append(
                Diagnostic(
                    "DTA330",
                    f"cannot load shared library {relative}: {exc}",
                    path=source.manifest_rel,
                    pointer=f"/spec/uses/{index}/path",
                )
            )
            continue
        library_manifest = library.manifest
        if not isinstance(library_manifest, dict):
            diagnostics.append(
                Diagnostic(
                    "DTA330",
                    f"shared library {relative} has no valid agent.yaml",
                    path=source.manifest_rel,
                    pointer=f"/spec/uses/{index}/path",
                )
            )
            continue
        metadata = library_manifest.get("metadata")
        version = metadata.get("version", "0.1.0") if isinstance(metadata, dict) else "0.1.0"
        if not isinstance(version, str) or parse_version(version) is None:
            diagnostics.append(
                Diagnostic(
                    "DTA332",
                    f"shared library {relative} has invalid version {version!r}",
                    path=source.manifest_rel,
                    pointer=f"/spec/uses/{index}/version",
                )
            )
            continue
        if isinstance(constraint, str):
            if not valid_constraint(constraint):
                diagnostics.append(
                    Diagnostic(
                        "DTA332",
                        f"invalid version constraint {constraint!r}",
                        path=source.manifest_rel,
                        pointer=f"/spec/uses/{index}/version",
                    )
                )
                continue
            if not matches(version, constraint):
                diagnostics.append(
                    Diagnostic(
                        "DTA331",
                        f"shared library {relative} is version {version}, "
                        f"which does not satisfy {constraint!r}",
                        path=source.manifest_rel,
                        pointer=f"/spec/uses/{index}/version",
                    )
                )
                continue
        name = library_root.name
        library_entities, inheritance_diagnostics = resolve_all_entities(
            library.entities
        )
        diagnostics.extend(inheritance_diagnostics)
        if (name, library.spec_dir) not in library_roots:
            library_roots.append((name, library.spec_dir))
        for kind in _IMPORTED_KINDS:
            for raw in library_entities.get(kind, ()):
                display = f"library:{name}/{raw.rel}"
                entity_diagnostics = validate_document(raw.data, raw.schema, path=display)
                if any(d.severity.value == "error" for d in entity_diagnostics):
                    diagnostics.extend(entity_diagnostics)
                    continue
                entity_name = _entity_name(raw)
                if not entity_name:
                    continue
                if entity_name in existing[kind]:
                    diagnostics.append(
                        Diagnostic(
                            "DTA333",
                            f"shared library {relative} defines duplicate {kind} {entity_name!r}",
                            path=display,
                        )
                    )
                    continue
                existing[kind].add(entity_name)
                imported[kind].append(
                    replace(
                        raw,
                        rel=f"libraries/{name}/{raw.rel}",
                        display=display,
                        extra_files=tuple(
                            f"libraries/{name}/{item}" for item in raw.extra_files
                        ),
                        source_root=library.spec_dir,
                    )
                )
    source.entities["tool"] = tuple(imported["tool"])
    source.entities["model"] = tuple(imported["model"])
    source.entities["skill"] = tuple(imported["skill"])
    source.entities["knowledge"] = tuple(imported["knowledge"])
    source.library_roots = tuple(library_roots)


def _entity_name(raw) -> str | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    name = data.get("name")
    if isinstance(name, str) and name:
        return name
    if raw.kind in {"tool", "model"}:
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
            return metadata["name"]
    if raw.kind == "skill":
        return Path(raw.rel).parent.name
    if raw.kind == "knowledge":
        path = Path(raw.rel)
        if path.parts and path.parts[0] == "libraries":
            path = Path(*path.parts[3:])
        return "-".join(path.with_suffix("").parts)
    return None


__all__ = ["attach_libraries"]
