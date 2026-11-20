"""Resolve ``extends`` inheritance and nested subagent declarations.

Inheritance is resolved on raw documents *before* schema validation, so the
merged document is what users see in diagnostics and what the compiler builds.
Every chain is acyclic and bounded, which keeps resolution decidable.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from dotagent.core.errors import Diagnostic
from dotagent.spec.loader import RawEntity

MAX_EXTENDS_CHAIN = 16
_INHERITABLE_KINDS = ("subagent", "tool", "model", "workflow", "task")


def resolve_all_entities(
    entities: dict[str, list[RawEntity]],
) -> tuple[dict[str, list[RawEntity]], list[Diagnostic]]:
    resolved: dict[str, list[RawEntity]] = {}
    diagnostics: list[Diagnostic] = []
    for kind, raws in entities.items():
        if kind in _INHERITABLE_KINDS:
            items, kind_diagnostics = resolve_inheritance(kind, raws)
            resolved[kind] = items
            diagnostics.extend(kind_diagnostics)
        else:
            resolved[kind] = list(raws)
    return resolved, diagnostics


def resolve_inheritance(
    kind: str, raws: list[RawEntity]
) -> tuple[list[RawEntity], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    by_name: dict[str, RawEntity] = {}
    for raw in raws:
        name = _entity_name(kind, raw)
        if name:
            by_name.setdefault(name, raw)
    cache: dict[str, RawEntity] = {}

    def resolve(raw: RawEntity, stack: tuple[str, ...]) -> RawEntity:
        name = _entity_name(kind, raw)
        if not name:
            return raw
        if name in cache:
            return cache[name]
        data = raw.data if isinstance(raw.data, dict) else None
        if not isinstance(data, dict):
            return raw
        base_name = data.get("extends")
        if base_name is None:
            cache[name] = raw
            return raw
        if not isinstance(base_name, str):
            diagnostics.append(
                Diagnostic(
                    "DTA320",
                    "'extends' must be a string naming an entity of the same kind",
                    path=raw.display,
                )
            )
            return _without_extends(raw)
        if base_name == name or base_name in stack:
            diagnostics.append(
                Diagnostic(
                    "DTA321",
                    f"circular extends chain: {' -> '.join((*stack, name, base_name))}",
                    path=raw.display,
                )
            )
            return _without_extends(raw)
        if len(stack) >= MAX_EXTENDS_CHAIN:
            diagnostics.append(
                Diagnostic(
                    "DTA322",
                    f"extends chain exceeds {MAX_EXTENDS_CHAIN} links",
                    path=raw.display,
                )
            )
            return _without_extends(raw)
        base_raw = by_name.get(base_name)
        if base_raw is None:
            diagnostics.append(
                Diagnostic(
                    "DTA301",
                    f"extends target {base_name!r} does not exist",
                    path=raw.display,
                )
            )
            return _without_extends(raw)
        base = resolve(base_raw, (*stack, name))
        merged = _merge_documents(kind, base.data, data, name)
        body = raw.body
        if kind == "subagent" and not (body or "").strip():
            body = base.body
        result = replace(raw, data=merged, body=body)
        cache[name] = result
        return result

    return [resolve(raw, ()) for raw in raws], diagnostics


def _entity_name(kind: str, raw: RawEntity) -> str | None:
    data = raw.data if isinstance(raw.data, dict) else None
    if not isinstance(data, dict):
        return None
    if kind == "subagent":
        name = data.get("name")
    else:
        metadata = data.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and name else None


def _without_extends(raw: RawEntity) -> RawEntity:
    if not isinstance(raw.data, dict) or "extends" not in raw.data:
        return raw
    data = dict(raw.data)
    data.pop("extends", None)
    return replace(raw, data=data)


def _merge_documents(
    kind: str, base: object, child: object, name: str
) -> dict:
    base_doc = deepcopy(base) if isinstance(base, dict) else {}
    child_doc = deepcopy(child) if isinstance(child, dict) else {}
    merged = _deep_merge(base_doc, child_doc)
    merged.pop("extends", None)
    if kind == "subagent":
        merged["name"] = name
    else:
        metadata = merged.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            merged["metadata"] = metadata
        child_metadata = child_doc.get("metadata")
        if isinstance(child_metadata, dict) and child_metadata.get("name"):
            metadata["name"] = child_metadata["name"]
        else:
            metadata.pop("name", None)
    return merged


def _deep_merge(base: object, child: object) -> object:
    if isinstance(base, dict) and isinstance(child, dict):
        merged = dict(base)
        for key, value in child.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(child, dict):
        return deepcopy(child)
    return deepcopy(child) if child is not None else deepcopy(base)


__all__ = [
    "MAX_EXTENDS_CHAIN",
    "resolve_all_entities",
    "resolve_inheritance",
]
