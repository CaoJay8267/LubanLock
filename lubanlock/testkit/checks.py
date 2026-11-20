"""User-authored declarative checks (``.agents/tests/checks.yaml``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from lubanlock.core.errors import Diagnostic, Severity
from lubanlock.spec.schema import CHECKS_SCHEMA, validate_document

from .base import CaseResult

CHECKS_PATH = "tests/checks.yaml"

_MISSING = object()


def json_equal(left: Any, right: Any) -> bool:
    """JSON equality: booleans are not numbers, lists compare element-wise."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def load_checks(root: Path) -> tuple[list[dict], list[Diagnostic]]:
    """Read and schema-validate the checks file.  Never raises."""

    path = root / CHECKS_PATH
    if not path.is_file():
        return [], []
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [
            Diagnostic("DTA103", f"cannot parse checks file: {exc}", path=CHECKS_PATH)
        ]
    if not isinstance(document, dict):
        return [], [
            Diagnostic("DTA202", "checks file must be a YAML mapping", path=CHECKS_PATH)
        ]
    diagnostics = validate_document(document, CHECKS_SCHEMA, path=CHECKS_PATH)
    checks = document.get("checks") or []
    if any(d.severity is Severity.ERROR for d in diagnostics):
        return [], diagnostics
    return [check for check in checks if isinstance(check, dict)], diagnostics


def run_checks(checks: list[dict], agent_dict: dict) -> list[CaseResult]:
    results: list[CaseResult] = []
    for index, check in enumerate(checks):
        check_id = check.get("id") or f"check-{index + 1}"
        case_id = f"custom/{check_id}"
        title = check.get("description") or f"check {check_id}"
        path = check.get("path", "")
        op = check.get("op", "")
        value = resolve_path(agent_dict, path)
        ok, message = evaluate(op, value, check.get("value", _MISSING), path)
        results.append(
            CaseResult(
                id=case_id,
                suite="custom",
                title=title,
                status="passed" if ok else "failed",
                message=message,
            )
        )
    return results


def resolve_path(data: Any, path: str) -> Any:
    if not path:
        return data
    current = data
    for token in path.split("."):
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return _MISSING
            current = current[int(token)]
        else:
            return _MISSING
    return current


def evaluate(op: str, value: Any, expected: Any, path: str) -> tuple[bool, str]:
    if op == "exists":
        return (value is not _MISSING, "" if value is not _MISSING else f"{path!r} does not exist")
    if op == "not_exists":
        return (value is _MISSING, "" if value is _MISSING else f"{path!r} exists")
    if value is _MISSING:
        return False, f"{path!r} does not exist"
    if expected is _MISSING:
        return False, f"op {op!r} requires a value"
    if op == "equals":
        if json_equal(value, expected):
            return True, ""
        return False, f"{path!r} is {value!r}, expected {expected!r}"
    if op == "contains":
        if isinstance(value, list):
            ok = expected in value
        elif isinstance(value, str):
            ok = str(expected) in value
        else:
            return False, f"op 'contains' needs a string or array at {path!r}"
        return (ok, "" if ok else f"{path!r} does not contain {expected!r}")
    if op == "matches":
        if not isinstance(expected, str):
            return False, "op 'matches' requires a string pattern"
        try:
            ok = re.search(expected, str(value)) is not None
        except re.error as exc:
            return False, f"invalid pattern: {exc}"
        return (ok, "" if ok else f"{path!r} does not match {expected!r}")
    if op in {"min_items", "max_items"}:
        if not isinstance(value, list):
            return False, f"op {op!r} needs an array at {path!r}"
        if not isinstance(expected, int):
            return False, f"op {op!r} requires an integer value"
        ok = len(value) >= expected if op == "min_items" else len(value) <= expected
        return (ok, "" if ok else f"{path!r} has {len(value)} items, expected {op} {expected}")
    if op in {"min_length", "max_length"}:
        if not isinstance(value, (str, list)):
            return False, f"op {op!r} needs a string or array at {path!r}"
        if not isinstance(expected, int):
            return False, f"op {op!r} requires an integer value"
        ok = len(value) >= expected if op == "min_length" else len(value) <= expected
        return (ok, "" if ok else f"{path!r} has length {len(value)}, expected {op} {expected}")
    return False, f"unknown op {op!r}"
