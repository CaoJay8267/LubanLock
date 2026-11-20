"""Declarative tests: built-in suites plus user-authored checks."""

from __future__ import annotations

from . import fixtures
from .base import CaseResult, TestReport
from .checks import load_checks, resolve_path, run_checks
from .runner import SUITES, run_tests

__all__ = [
    "SUITES",
    "CaseResult",
    "TestReport",
    "fixtures",
    "load_checks",
    "resolve_path",
    "run_checks",
    "run_tests",
]
