"""Small, dependency-light helpers shared across LubanLock."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent

#: Refuse to read source files larger than this (protects against YAML bombs and
#: accidental binary files). Assets are limited separately.
MAX_SOURCE_BYTES = 8 * 1024 * 1024

#: Media types used when packaging/projecting assets.
_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".py": "text/x-python",
    ".toml": "application/toml",
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".sh": "application/x-sh",
    ".ps1": "application/x-powershell",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
}


class FrontMatterError(ValueError):
    """Raised when a file starts with ``---`` but has no closing fence."""


class UnsafePathError(ValueError):
    """Raised when a declared path escapes its containing directory."""


class _PlainSafeLoader(yaml.SafeLoader):
    """SafeLoader that keeps ISO timestamps as strings.

    LubanLock documents are data, not code: a date such as ``2026-01-01`` must
    stay a string so the compiled IR is always JSON-serializable and stable.
    YAML aliases are rejected to rule out alias-expansion blow-ups in
    serialized artifacts.
    """

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ConstructorError(
                None,
                None,
                "YAML aliases are not supported in LubanLock documents",
                event.start_mark,
            )
        return super().compose_node(parent, index)


_PlainSafeLoader.yaml_implicit_resolvers = {
    key: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def safe_load_yaml(text: str) -> object:
    """Parse YAML with LubanLock's deterministic, JSON-friendly loader."""

    return yaml.load(text, Loader=_PlainSafeLoader)


def canonical_json(value: object) -> str:
    """Serialize *value* deterministically (sorted keys, compact separators)."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def pretty_json(value: object) -> str:
    """Serialize *value* deterministically for humans/artifacts."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def read_text(path: Path, *, max_bytes: int = MAX_SOURCE_BYTES) -> str:
    """Read a UTF-8 text file, refusing oversized or undecodable content."""

    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"file is {size} bytes, exceeding the {max_bytes} byte limit"
        )
    data = path.read_bytes()
    return data.decode("utf-8-sig")


_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class SplitDocument:
    """A parsed front-matter document."""

    front_matter: dict | None
    body: str


def split_front_matter(text: str) -> SplitDocument:
    """Split optional YAML front matter from a Markdown body.

    A file that starts with ``---`` must close the fence; otherwise
    :class:`FrontMatterError` is raised.  Documents without a leading fence are
    treated as body-only.
    """

    if not text.startswith("---"):
        return SplitDocument(None, text)
    match = _FRONT_MATTER.match(text)
    if not match:
        raise FrontMatterError(
            "file starts with '---' but has no closing '---' front matter fence"
        )
    try:
        data = safe_load_yaml(match.group(1))
    except yaml.YAMLError as exc:  # pragma: no cover - message formatting differs
        raise FrontMatterError(f"invalid YAML front matter: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontMatterError("front matter must be a YAML mapping")
    body = text[match.end() :]
    return SplitDocument(data, body.removeprefix("\n"))


def resolve_relative(base: Path, relative: str, *, field: str) -> Path:
    """Resolve *relative* inside *base*, rejecting escapes and absolute paths."""

    rel = relative.strip().replace("\\", "/")
    if not rel:
        raise UnsafePathError(f"{field}: path must not be empty")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise UnsafePathError(f"{field}: path must be relative, got {relative!r}")
    if any(part == ".." for part in rel.split("/")):
        raise UnsafePathError(f"{field}: path must not contain '..', got {relative!r}")
    base_resolved = base.resolve()
    candidate = (base_resolved / rel).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise UnsafePathError(f"{field}: path escapes {base}, got {relative!r}")
    return candidate


def ensure_inside(base: Path, candidate: Path) -> Path:
    """Return *candidate* if it is inside *base*, else raise."""

    base_resolved = base.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise UnsafePathError(f"path escapes {base}: {candidate}")
    return resolved


def to_posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def media_type_for(path: str | Path) -> str:
    return _MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def slugify(value: str) -> str:
    """Turn an arbitrary file stem into a valid LubanLock name."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = "item"
    if not slug[0].isalpha():
        slug = f"item-{slug}"
    return slug[:64].rstrip("-")


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Return matching files under *root*, sorted by POSIX relative path."""

    if not root.is_dir():
        return []
    found = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes and not _ignored(p)
    ]
    return sorted(found, key=lambda p: to_posix(p.relative_to(root)))


def iter_all_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = [p for p in root.rglob("*") if p.is_file() and not _ignored(p)]
    return sorted(found, key=lambda p: to_posix(p.relative_to(root)))


def _ignored(path: Path) -> bool:
    parts = path.parts
    return any(part in {"__pycache__", ".git", ".idea", ".venv"} for part in parts)
