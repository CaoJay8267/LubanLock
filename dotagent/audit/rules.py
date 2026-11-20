"""Pluggable audit rules.

Third-party rules register through the ``dotagent.audit_rules`` entry point and
may be enabled/disabled or re-graded through ``.agents/audit.yaml`` just like the
built-in rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points
from typing import ClassVar

from dotagent.audit.base import AuditFinding
from dotagent.audit.config import AuditConfig
from dotagent.core.models import AgentDefinition
from dotagent.spec.schema import get_schema_store
from dotagent.spec.schema.validator import validate_instance


class AuditRule(ABC):
    code: str = ""
    description: str = ""
    parameter_schema: ClassVar[dict] = {}

    @abstractmethod
    def run(
        self, definition: AgentDefinition, config: AuditConfig
    ) -> list[AuditFinding]:
        """Return findings for one agent definition."""


_REGISTRY: dict[str, AuditRule] = {}
_EXTERNAL_LOADED = False


def register_audit_rule(rule: AuditRule) -> None:
    if not rule.code:
        raise ValueError("audit rule must declare a code")
    _REGISTRY[rule.code] = rule


def _load_external() -> None:
    global _EXTERNAL_LOADED
    if _EXTERNAL_LOADED:
        return
    _EXTERNAL_LOADED = True
    try:
        discovered = entry_points(group="dotagent.audit_rules")
    except Exception:  # noqa: BLE001  # pragma: no cover - importlib edge cases
        return
    for entry in discovered:
        try:
            loaded = entry.load()
            rule = loaded() if isinstance(loaded, type) else loaded
        except Exception:  # noqa: BLE001,S112  # pragma: no cover - broken plugin
            continue
        if isinstance(rule, AuditRule) and rule.code:
            _REGISTRY[rule.code] = rule


def audit_rules() -> dict[str, AuditRule]:
    _load_external()
    return dict(_REGISTRY)


def run_custom_rules(
    definition: AgentDefinition,
    config: AuditConfig,
    existing_codes: set[str] | None = None,
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    rules = audit_rules()
    triggered = set(existing_codes or ())
    pending = set(rules)
    ordered: list[str] = []
    while pending:
        ready = sorted(
            code
            for code in pending
            if all(
                dependency in ordered
                or dependency in triggered
                or dependency not in rules
                for dependency in config.depends_on(code)
            )
        )
        if not ready:
            for code in sorted(pending):
                findings.append(
                    AuditFinding(
                        code,
                        "warning",
                        f"audit rule {code} has a dependency cycle",
                    )
                )
            break
        for code in ready:
            pending.remove(code)
            ordered.append(code)
    for code in ordered:
        rule = rules[code]
        if not config.enabled(code):
            continue
        dependencies = config.depends_on(code)
        if any(dependency not in triggered for dependency in dependencies):
            continue
        when = config.when(code)
        if when and not any(code_name in triggered for code_name in when):
            continue
        unless = config.unless(code)
        if unless and any(code_name in triggered for code_name in unless):
            continue
        try:
            parameters = config.parameters(code)
            if rule.parameter_schema:
                store = get_schema_store()
                parameter_diagnostics = validate_instance(
                    parameters,
                    rule.parameter_schema,
                    store=store,
                    base_file=store.path_for("audit.schema.json"),
                )
                if any(d.severity.value == "error" for d in parameter_diagnostics):
                    findings.append(
                        AuditFinding(
                            code,
                            "warning",
                            "invalid audit rule parameters: "
                            + "; ".join(d.message for d in parameter_diagnostics),
                            path=".agents/audit.yaml",
                        )
                    )
                    continue
            rule_findings = rule.run(definition, config)
            findings.extend(rule_findings)
            triggered.update(finding.code for finding in rule_findings)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not crash audit
            findings.append(
                AuditFinding(
                    code,
                    "warning",
                    f"audit rule {code} failed: {exc}",
                )
            )
    return findings


__all__ = [
    "AuditRule",
    "audit_rules",
    "register_audit_rule",
    "run_custom_rules",
]
