"""Projector registry, including third-party projectors via entry points."""

from __future__ import annotations

from importlib.metadata import entry_points

from dotagent.core.errors import ProjectionError

from .base import Projector

_BUILTIN: dict[str, Projector] = {}
_EXTERNAL: dict[str, Projector] = {}


def register(projector: Projector) -> None:
    if not projector.name:
        raise ProjectionError("projector must declare a non-empty name")
    _BUILTIN[projector.name] = projector


def _load_external() -> dict[str, Projector]:
    if _EXTERNAL:
        return _EXTERNAL
    try:
        discovered = entry_points(group="dotagent.projectors")
    except Exception:  # noqa: BLE001  # pragma: no cover - importlib edge cases
        discovered = []
    for entry in discovered:
        try:
            loaded = entry.load()
            instance = loaded() if isinstance(loaded, type) else loaded
        except Exception as exc:  # pragma: no cover - broken plugin
            raise ProjectionError(
                f"cannot load projector plugin {entry.name!r}: {exc}"
            ) from exc
        if not isinstance(instance, Projector):
            raise ProjectionError(
                f"projector plugin {entry.name!r} is not a Projector instance"
            )
        if not instance.name:
            instance.name = entry.name
        _EXTERNAL[instance.name] = instance
    return _EXTERNAL


def get_projector(name: str) -> Projector:
    if name in _BUILTIN:
        return _BUILTIN[name]
    external = _load_external()
    if name in external:
        return external[name]
    available = ", ".join(sorted(set(_BUILTIN) | set(external))) or "none"
    raise ProjectionError(
        f"unknown projection target {name!r}; available targets: {available}"
    )


def list_projectors() -> list[dict]:
    entries: dict[str, dict] = {}
    for name, projector in _BUILTIN.items():
        entries[name] = {**projector.to_dict(), "source": "builtin"}
    try:
        external = _load_external()
    except ProjectionError:
        external = {}
    for name, projector in external.items():
        entries[name] = {**projector.to_dict(), "source": "plugin"}
    return [entries[name] for name in sorted(entries)]
