"""Small SemVer helper used by shared-library constraints.

Only the common comparison forms are supported; anything else is rejected by
validation rather than guessed at.
"""

from __future__ import annotations

import re

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def matches(version: str, constraint: str) -> bool:
    """Return whether *version* satisfies a comma-separated *constraint*."""

    parsed = parse_version(version)
    if parsed is None:
        return False
    for raw in constraint.split(","):
        item = raw.strip()
        if not item:
            continue
        if not _matches_one(parsed, item):
            return False
    return True


def valid_constraint(constraint: str) -> bool:
    try:
        for raw in constraint.split(","):
            item = raw.strip()
            if item:
                _matches_one((0, 0, 0), item)
        return True
    except ValueError:
        return False


def _matches_one(version: tuple[int, int, int], item: str) -> bool:
    match = re.match(r"^(>=|<=|==|!=|>|<|~=|\^)?\s*(.+)$", item)
    if not match:
        raise ValueError(f"invalid version constraint {item!r}")
    operator = match.group(1) or "=="
    target = parse_version(match.group(2))
    if target is None:
        raise ValueError(f"invalid version in constraint {item!r}")
    if operator == ">=":
        return version >= target
    if operator == "<=":
        return version <= target
    if operator == ">":
        return version > target
    if operator == "<":
        return version < target
    if operator == "==":
        return version == target
    if operator == "!=":
        return version != target
    if operator == "~=":
        lower = target
        upper = (target[0], target[1] + 1, 0)
        return lower <= version < upper
    if operator == "^":
        major, minor, patch = target
        if major > 0:
            upper = (major + 1, 0, 0)
        elif minor > 0:
            upper = (0, minor + 1, 0)
        else:
            upper = (0, 0, patch + 1)
        return target <= version < upper
    raise ValueError(f"unsupported version operator {operator!r}")


__all__ = ["matches", "parse_version", "valid_constraint"]
