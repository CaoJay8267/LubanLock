"""Explicit cross-agent shared libraries (``spec.uses``).

A library is another DotAgent project.  Only its ``tools`` and ``models`` are
imported; both are pure declarations without assets, so the importer never needs
to copy files.  Names must not collide, versions are checked with the small
SemVer helper, and the dependency list is explicit (no discovery).
"""

from __future__ import annotations

from dataclasses import replace

from dotagent.core.errors import Diagnostic
from dotagent.core.semver import matches, parse_version, valid_constraint
from dotagent.spec.inheritance import resolve_all_entities
from dotagent.spec.loader import AgentSource, load_source
from dotagent.spec.schema import validate_document

_IMPORTED_KINDS = ("tool", "model")


def attach_libraries(
    source: AgentSource, manifest: dict, diagnostics
) -> None:
    """Load libraries declared in ``spec.uses`` into *source*."""

    spec = manifest.get("spec")
    uses = spec.get("uses") if isinstance(spec, dict) else None
    if not isinstance(uses, list):
        return
    existing: dict[str, set[str]] = {"tool": set(), "model": set()}
    # Seed with the main project's names so imports cannot shadow local entities.
    for entity in source.entities.get("tool", ()):
        if isinstance(entity.data, dict):
            metadata = entity.data.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
                existing["tool"].add(metadata["name"])
    for entity in source.entities.get("model", ()):
        if isinstance(entity.data, dict):
            metadata = entity.data.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
                existing["model"].add(metadata["name"])

    imported: dict[str, list] = {"tool": list(source.entities.get("tool", ())), "model": list(source.entities.get("model", ()))}
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
        for kind in _IMPORTED_KINDS:
            for raw in library_entities.get(kind, ()):
                display = f"library:{name}/{raw.rel}"
                entity_diagnostics = validate_document(raw.data, raw.schema, path=display)
                if any(d.severity.value == "error" for d in entity_diagnostics):
                    diagnostics.extend(entity_diagnostics)
                    continue
                data = raw.data if isinstance(raw.data, dict) else {}
                entity_metadata = data.get("metadata") if isinstance(data, dict) else None
                entity_name = entity_metadata.get("name") if isinstance(entity_metadata, dict) else None
                if not isinstance(entity_name, str):
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
                    replace(raw, rel=f"library:{name}/{raw.rel}", display=display)
                )
    source.entities["tool"] = tuple(imported["tool"])
    source.entities["model"] = tuple(imported["model"])


__all__ = ["attach_libraries"]
