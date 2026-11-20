"""Declarative testing: results, checks and the built-in suites."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaseResult:
    id: str
    suite: str
    title: str
    status: str  # passed | failed | skipped
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "suite": self.suite,
            "title": self.title,
            "status": self.status,
        }
        if self.message:
            data["message"] = self.message
        return data


@dataclass(frozen=True)
class TestReport:
    agent: str
    fingerprint: str
    cases: tuple[CaseResult, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not any(case.status == "failed" for case in self.cases)

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(case for case in self.cases if case.status == "failed")

    def counts(self) -> dict[str, int]:
        result = {"passed": 0, "failed": 0, "skipped": 0}
        for case in self.cases:
            result[case.status] = result.get(case.status, 0) + 1
        return result

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "agent": self.agent,
            "fingerprint": self.fingerprint,
            "counts": self.counts(),
            "cases": [case.to_dict() for case in self.cases],
        }
