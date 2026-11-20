"""Diagnostics and exception types for LubanLock.

Every problem LubanLock reports is a :class:`Diagnostic` carrying a stable code,
a severity, a human message and (when known) the source file and JSON pointer.
Codes are grouped by stage:

===========  =========================================================
Range        Stage
===========  =========================================================
``DTA1xx``   loading / IO
``DTA2xx``   JSON Schema validation
``DTA3xx``   domain validation (references, workflows, schemas)
``DTA4xx``   limits / budgets
``DTA5xx``   projection
``DTA6xx``   packaging / verification
``DTA7xx``   declarative tests
``DTA9xx``   lint warnings
===========  =========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Diagnostic:
    """A single structured problem report."""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    path: str | None = None
    pointer: str = ""
    hint: str | None = None

    def format(self, *, color: bool = False) -> str:
        level = self.severity.value
        if color:
            colors = {"error": "\x1b[31m", "warning": "\x1b[33m", "info": "\x1b[36m"}
            level = f"{colors[self.severity.value]}{level}\x1b[0m"
        location = self.path or ""
        if location and self.pointer:
            location = f"{location} {self.pointer}"
        elif self.pointer:
            location = self.pointer
        head = f"[{level}] {self.code}"
        if location:
            head = f"{head} {location}"
        text = f"{head}: {self.message}"
        if self.hint:
            text = f"{text}\n         hint: {self.hint}"
        return text

    def to_dict(self) -> dict:
        data = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.path:
            data["path"] = self.path
        if self.pointer:
            data["pointer"] = self.pointer
        if self.hint:
            data["hint"] = self.hint
        return data


@dataclass
class ValidationResult:
    """Ordered collection of diagnostics plus convenience accessors."""

    diagnostics: list[Diagnostic] = field(default_factory=list)

    def error(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        pointer: str = "",
        hint: str | None = None,
    ) -> Diagnostic:
        diag = Diagnostic(code, message, Severity.ERROR, path, pointer, hint)
        self.diagnostics.append(diag)
        return diag

    def warning(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        pointer: str = "",
        hint: str | None = None,
    ) -> Diagnostic:
        diag = Diagnostic(code, message, Severity.WARNING, path, pointer, hint)
        self.diagnostics.append(diag)
        return diag

    def info(self, code: str, message: str, **kwargs) -> Diagnostic:
        diag = Diagnostic(code, message, Severity.INFO, **kwargs)
        self.diagnostics.append(diag)
        return diag

    def extend(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics.extend(diagnostics)

    def sorted(self) -> list[Diagnostic]:
        rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        return sorted(
            self.diagnostics,
            key=lambda d: (rank[d.severity], d.path or "", d.pointer, d.code, d.message),
        )

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self, message: str = "validation failed") -> None:
        if self.errors:
            raise ValidationFailed(message, diagnostics=self.sorted())

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": sum(1 for d in self.diagnostics if d.severity is Severity.ERROR),
            "warnings": sum(1 for d in self.diagnostics if d.severity is Severity.WARNING),
            "diagnostics": [d.to_dict() for d in self.sorted()],
        }


class LubanLockError(Exception):
    """Base class for every error raised by LubanLock."""

    code = "DTA000"

    def __init__(self, message: str, *, diagnostics: list[Diagnostic] | None = None):
        super().__init__(message)
        self.message = message
        self.diagnostics = diagnostics or []


class LoadError(LubanLockError):
    """The source tree could not be read or parsed."""

    code = "DTA100"


class ValidationFailed(LubanLockError):
    """Structured validation found errors."""

    code = "DTA200"


class ProjectionError(LubanLockError):
    """A projector could not produce a valid projection."""

    code = "DTA500"


class PackageError(LubanLockError):
    """Packaging or verification failed."""

    code = "DTA600"


class TestRunError(LubanLockError):
    """The declarative test runner could not run."""

    code = "DTA700"


class UsageError(LubanLockError):
    """Invalid command line usage."""

    code = "DTA900"
