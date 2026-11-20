"""Content-hash cache for incremental builds.

The lockfile records content hashes.  On an incremental build, DotAgent first
consults ``.agents/.cache/sources.json``: an unchanged stat signature reuses the
cached hash, a changed mtime forces a re-hash, and a changed hash forces a
rebuild.  This means touching a file without changing it no longer invalidates
the build.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotagent.build.lockfile import scan_sources
from dotagent.core.utils import sha256_bytes

CACHE_FORMAT = "dotagent-cache/1"
CACHE_FILE = ".cache/sources.json"


def cache_path(spec_dir: str | Path) -> Path:
    return Path(spec_dir) / CACHE_FILE


def read_cache(spec_dir: str | Path) -> dict[str, dict]:
    path = cache_path(spec_dir)
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict) or document.get("format") != CACHE_FORMAT:
        return {}
    sources = document.get("sources")
    return sources if isinstance(sources, dict) else {}


def write_cache(spec_dir: str | Path, sources: dict[str, dict]) -> Path:
    path = cache_path(spec_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"format": CACHE_FORMAT, "sources": sources},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def compute_hashes(
    project_root: str | Path,
    spec_dir: str | Path,
    cached: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Return ``rel -> {size, mtime_ns, sha256}`` using cached hashes."""

    root = Path(project_root)
    cached = cached or {}
    stats = scan_sources(root, Path(spec_dir))
    result: dict[str, dict] = {}
    for relative, stat in stats.items():
        previous = cached.get(relative)
        if (
            isinstance(previous, dict)
            and previous.get("size") == stat["size"]
            and previous.get("mtime_ns") == stat["mtime_ns"]
            and isinstance(previous.get("sha256"), str)
        ):
            digest = previous["sha256"]
        else:
            digest = sha256_bytes((root / relative).read_bytes())
        result[relative] = {
            "size": stat["size"],
            "mtime_ns": stat["mtime_ns"],
            "sha256": digest,
        }
    return result


def refresh_cache(
    project_root: str | Path, spec_dir: str | Path
) -> dict[str, dict]:
    sources = compute_hashes(project_root, spec_dir, read_cache(spec_dir))
    write_cache(spec_dir, sources)
    return sources


def content_matches_lock(
    sources: dict[str, dict], lock: dict
) -> bool:
    expected = lock.get("sources")
    if not isinstance(expected, dict) or set(sources) != set(expected):
        return False
    for relative, meta in expected.items():
        if not isinstance(meta, dict):
            return False
        if sources[relative].get("sha256") != meta.get("sha256"):
            return False
    return True


def incremental_status(
    project_root: str | Path,
    spec_dir: str | Path,
    lock: dict | None,
    output_agent_json: str | Path,
) -> dict | None:
    """Return lock data when the build can be reused, else None."""

    if not lock or not Path(output_agent_json).is_file():
        return None
    sources = compute_hashes(project_root, spec_dir, read_cache(spec_dir))
    write_cache(spec_dir, sources)
    if not content_matches_lock(sources, lock):
        return None
    try:
        output = json.loads(Path(output_agent_json).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if output.get("fingerprint") != lock.get("fingerprint"):
        return None
    return lock


__all__ = [
    "CACHE_FILE",
    "CACHE_FORMAT",
    "cache_path",
    "compute_hashes",
    "content_matches_lock",
    "incremental_status",
    "read_cache",
    "refresh_cache",
    "write_cache",
]
