"""Shared audit result types (kept separate to avoid import cycles)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str  # error | warning | info
    message: str
    path: str | None = None
    pointer: str = ""

    def to_dict(self) -> dict:
        data = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.path:
            data["path"] = self.path
        if self.pointer:
            data["pointer"] = self.pointer
        return data


__all__ = ["AuditFinding"]
