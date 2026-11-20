"""Configurable audit rules loaded from ``.agents/audit.yaml``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml

from lubanlock.core.errors import Diagnostic
from lubanlock.core.utils import safe_load_yaml
from lubanlock.spec.schema import AUDIT_SCHEMA, validate_document

AUDIT_FILE = "audit.yaml"
_SEVERITIES = {"error", "warning", "info"}


@dataclass(frozen=True)
class RuleSetting:
    enabled: bool = True
    severity: str | None = None
    category: str | None = None
    parameters: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    when: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditConfig:
    rules: dict[str, RuleSetting] = field(default_factory=dict)
    secret_patterns: tuple[str, ...] = ()
    risky_tool_names: tuple[str, ...] = ()
    budget_warning_ratio: float = 0.8
    ignore_paths: tuple[str, ...] = ()

    def enabled(self, code: str) -> bool:
        setting = self.rules.get(code)
        return True if setting is None else setting.enabled

    def severity(self, code: str, default: str) -> str:
        setting = self.rules.get(code)
        if setting is not None and setting.severity:
            return setting.severity
        return default

    def category(self, code: str, default: str | None = None) -> str | None:
        setting = self.rules.get(code)
        if setting is not None and setting.category:
            return setting.category
        return default

    def parameters(self, code: str) -> dict:
        setting = self.rules.get(code)
        return dict(setting.parameters) if setting is not None else {}

    def depends_on(self, code: str) -> tuple[str, ...]:
        setting = self.rules.get(code)
        return setting.depends_on if setting is not None else ()

    def when(self, code: str) -> tuple[str, ...]:
        setting = self.rules.get(code)
        return setting.when if setting is not None else ()

    def unless(self, code: str) -> tuple[str, ...]:
        setting = self.rules.get(code)
        return setting.unless if setting is not None else ()

    def ignored(self, path: str | None) -> bool:
        if not path:
            return False
        return any(fnmatch(path, pattern) for pattern in self.ignore_paths)


def load_audit_config(spec_dir: str | Path) -> tuple[AuditConfig, list[Diagnostic]]:
    """Load and validate the optional audit configuration."""

    path = Path(spec_dir) / AUDIT_FILE
    if not path.is_file():
        return AuditConfig(), []
    display = f"{Path(spec_dir).name}/{AUDIT_FILE}"
    try:
        document = safe_load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return AuditConfig(), [Diagnostic("DTA103", f"cannot parse audit.yaml: {exc}", path=display)]
    diagnostics = validate_document(document, AUDIT_SCHEMA, path=display)
    if diagnostics:
        return AuditConfig(), diagnostics
    if not isinstance(document, dict):
        return AuditConfig(), diagnostics
    rules: dict[str, RuleSetting] = {}
    for code, value in (document.get("rules") or {}).items():
        if not isinstance(value, dict):
            continue
        severity = value.get("severity")
        category = value.get("category")
        parameters = value.get("parameters")
        depends_on = value.get("depends_on")
        when = value.get("when")
        unless = value.get("unless")
        rules[str(code)] = RuleSetting(
            enabled=bool(value.get("enabled", True)),
            severity=severity if severity in _SEVERITIES else None,
            category=str(category) if isinstance(category, str) and category else None,
            parameters=parameters if isinstance(parameters, dict) else {},
            depends_on=tuple(str(item) for item in depends_on or ()),
            when=tuple(str(item) for item in when or ()),
            unless=tuple(str(item) for item in unless or ()),
        )
    patterns = tuple(str(item) for item in document.get("secret_patterns") or ())
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"invalid secret pattern {pattern!r}: {exc}",
                    path=display,
                )
            )
    ratio = document.get("budget_warning_ratio", 0.8)
    config = AuditConfig(
        rules=rules,
        secret_patterns=patterns,
        risky_tool_names=tuple(str(item) for item in document.get("risky_tool_names") or ()),
        budget_warning_ratio=float(ratio),
        ignore_paths=tuple(str(item) for item in document.get("ignore_paths") or ()),
    )
    return config, diagnostics


__all__ = ["AUDIT_FILE", "AuditConfig", "RuleSetting", "load_audit_config"]
