"""Lockfile support and a fast incremental-build check.

The lockfile records the compiled fingerprint plus a SHA-256 for every source
file.  ``dotagent compile --locked`` refuses to build when the lock and source
disagree; ``--incremental`` uses a stat signature for a cheap up-to-date check
and verifies the output fingerprint before reusing it.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotagent._version import __version__
from dotagent.build.compiler import CompiledAgent
from dotagent.core.errors import Diagnostic, LoadError
from dotagent.core.paths import AGENTS_FILE
from dotagent.core.utils import sha256_bytes, to_posix

LOCK_NAME = "dotagent.lock"
LOCK_FORMAT = "dotagent-lock/1"

_SOURCE_DIRS = ("knowledge", "skills", "agents", "subagents", "tools", "models", "workflows", "tasks")
_SOURCE_FILES = ("agent.yaml", "agent.yml", "instructions.md", "mcp.json")
_SKIP_PARTS = {"tests", "__pycache__", "build", "dist", "reports", ".git", "dotagent.lock"}


def lock_path(project_root: str | Path, spec_dir: str | Path) -> Path:
    return Path(spec_dir) / LOCK_NAME


def default_lock_path(compiled: CompiledAgent) -> Path:
    return compiled.definition.spec_dir / LOCK_NAME


def scan_sources(project_root: Path, spec_dir: Path) -> dict[str, dict]:
    """Return a stat signature for every source file that affects a build."""

    project_root = project_root.resolve()
    spec_dir = spec_dir.resolve()
    paths: list[Path] = []
    agents_md = project_root / AGENTS_FILE
    if agents_md.is_file():
        paths.append(agents_md)
    if spec_dir.is_dir():
        for path in spec_dir.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(project_root).parts
            if any(
                part in _SKIP_PARTS
                or (part.startswith(".") and part not in {".agents", ".agent"})
                for part in relative_parts
            ):
                continue
            paths.append(path)
    result: dict[str, dict] = {}
    for path in paths:
        rel = to_posix(path.relative_to(project_root))
        stat = path.stat()
        result[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return dict(sorted(result.items()))


def create_lock(compiled: CompiledAgent) -> dict:
    definition = compiled.definition
    stats = scan_sources(definition.root, definition.spec_dir)
    sources: dict[str, dict] = {}
    for rel, stat in stats.items():
        data = (definition.root / rel).read_bytes()
        sources[rel] = {
            "sha256": sha256_bytes(data),
            "size": stat["size"],
            "mtime_ns": stat["mtime_ns"],
        }
    return {
        "format": LOCK_FORMAT,
        "generator": {"name": "dotagent", "version": __version__},
        "agent": definition.metadata.name,
        "fingerprint": compiled.fingerprint,
        "sources": sources,
        "uses": [
            {"path": path, "version": version}
            for path, version in definition.uses
        ],
    }


def write_lock(
    compiled: CompiledAgent, path: str | Path | None = None
) -> Path:
    destination = Path(path) if path else default_lock_path(compiled)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(create_lock(compiled), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def read_lock(path: str | Path) -> dict | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadError(f"cannot read lockfile {candidate}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != LOCK_FORMAT:
        raise LoadError(f"unsupported lockfile format in {candidate}")
    return data


def check_lock(compiled: CompiledAgent, lock: dict) -> list[str]:
    """Return a list of human-readable mismatches (empty means valid)."""

    problems: list[str] = []
    if lock.get("fingerprint") != compiled.fingerprint:
        problems.append(
            "fingerprint changed: "
            f"lock={lock.get('fingerprint')} compiled={compiled.fingerprint}"
        )
    sources = lock.get("sources")
    if not isinstance(sources, dict):
        return problems + ["lockfile has no sources object"]
    current = scan_sources(compiled.definition.root, compiled.definition.spec_dir)
    if set(current) != set(sources):
        missing = sorted(set(sources) - set(current))
        added = sorted(set(current) - set(sources))
        if missing:
            problems.append("lockfile lists missing sources: " + ", ".join(missing))
        if added:
            problems.append("sources not in the lockfile: " + ", ".join(added))
    for rel, meta in sources.items():
        if rel not in current:
            continue
        data = (compiled.definition.root / rel).read_bytes()
        if not isinstance(meta, dict) or meta.get("sha256") != sha256_bytes(data):
            problems.append(f"source hash mismatch: {rel}")
    return problems


def reusable_output(
    project_root: str | Path,
    spec_dir: str | Path,
    lock: dict,
    output_agent_json: str | Path,
) -> bool:
    """Cheap stat-based check used by ``dotagent compile --incremental``."""

    project_root = Path(project_root)
    spec_dir = Path(spec_dir)
    if not Path(output_agent_json).is_file():
        return False
    sources = lock.get("sources")
    if not isinstance(sources, dict):
        return False
    current = scan_sources(project_root, spec_dir)
    if set(current) != set(sources):
        return False
    for rel, meta in sources.items():
        if not isinstance(meta, dict):
            return False
        if current[rel]["size"] != meta.get("size"):
            return False
        if current[rel]["mtime_ns"] != meta.get("mtime_ns"):
            return False
    try:
        output = json.loads(Path(output_agent_json).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return output.get("fingerprint") == lock.get("fingerprint")


def lock_diagnostics(problems: list[str], path: str = LOCK_NAME) -> list[Diagnostic]:
    return [Diagnostic("DTA601", problem, path=path) for problem in problems]


__all__ = [
    "LOCK_FORMAT",
    "LOCK_NAME",
    "check_lock",
    "create_lock",
    "default_lock_path",
    "lock_diagnostics",
    "lock_path",
    "read_lock",
    "reusable_output",
    "scan_sources",
    "write_lock",
]
