"""Projection primitives shared by every target."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lubanlock.build.compiler import CompiledAgent
from lubanlock.core.errors import Diagnostic, ProjectionError
from lubanlock.core.utils import media_type_for


@dataclass(frozen=True)
class ProjectedFile:
    path: str
    data: bytes
    media_type: str = "application/octet-stream"

    @classmethod
    def text(
        cls, path: str, content: str, *, media_type: str | None = None
    ) -> ProjectedFile:
        return cls(
            path=path,
            data=content.encode("utf-8"),
            media_type=media_type or media_type_for(path),
        )


@dataclass(frozen=True)
class ProjectionResult:
    target: str
    files: tuple[ProjectedFile, ...]
    warnings: tuple[Diagnostic, ...] = ()

    def file_map(self) -> dict[str, bytes]:
        return {file.path: file.data for file in self.files}

    def write(self, out: str | Path) -> list[Path]:
        """Write the projection below *out*, rejecting unsafe paths."""

        root = Path(out)
        written: list[Path] = []
        seen: set[str] = set()
        for projected in self.files:
            relative = PurePosixPath(projected.path)
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ProjectionError(
                    f"projector produced an unsafe path: {projected.path!r}"
                )
            if projected.path in seen:
                raise ProjectionError(
                    f"projector produced duplicate path: {projected.path!r}"
                )
            seen.add(projected.path)
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(projected.data)
            written.append(destination)
        return written


class Projector(ABC):
    """A pure transformation from a compiled agent to a set of files."""

    name: str = ""
    description: str = ""
    version: str = "1"

    @abstractmethod
    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        """Return the projected files.  Must not touch the filesystem."""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }
