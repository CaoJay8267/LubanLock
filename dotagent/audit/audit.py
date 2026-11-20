"""Static audit of an agent definition.

The audit is deliberately offline: it combines validation diagnostics with
heuristics for risky tools, insecure endpoints, leaked-looking secrets and
budget pressure.  It never executes tools or calls a model.  Rules can be
configured in ``.agents/audit.yaml``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotagent.audit.base import AuditFinding
from dotagent.audit.config import AuditConfig, load_audit_config
from dotagent.audit.rules import run_custom_rules
from dotagent.build.compiler import compile_agent
from dotagent.core.errors import Diagnostic
from dotagent.core.models import AgentDefinition
from dotagent.spec.loader import load_source
from dotagent.spec.validator import validate_source

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)

_DEFAULT_RISKY_TOOL_NAMES = ("shell", "exec", "bash", "powershell", "cmd", "run_command")


@dataclass(frozen=True)
class AuditReport:
    agent: str
    root: str
    fingerprint: str | None
    generated_at: str
    findings: tuple[AuditFinding, ...]
    stats: dict
    files: tuple[str, ...] = ()
    dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)
    conditions: dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    def counts(self) -> dict[str, int]:
        result = {"error": 0, "warning": 0, "info": 0}
        for finding in self.findings:
            result[finding.severity] = result.get(finding.severity, 0) + 1
        return result

    def categories(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for finding in self.findings:
            category = finding.category or "other"
            bucket = result.setdefault(category, {"error": 0, "warning": 0, "info": 0})
            bucket[finding.severity] = bucket.get(finding.severity, 0) + 1
        return result

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "root": self.root,
            "fingerprint": self.fingerprint,
            "generatedAt": self.generated_at,
            "ok": self.ok,
            "counts": self.counts(),
            "categories": self.categories(),
            "dependencies": {
                code: list(items) for code, items in self.dependencies.items()
            },
            "conditions": self.conditions,
            "stats": self.stats,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def to_markdown(self) -> str:
        counts = self.counts()
        lines = [
            f"# 审计报告：{self.agent}",
            "",
            f"- 生成时间（UTC）：`{self.generated_at}`",
            f"- 项目根目录：`{self.root}`",
            f"- 指纹：`{self.fingerprint or '未编译'}`",
            f"- 结论：{'通过' if self.ok else '存在错误'}",
            (
                f"- 问题：{counts.get('error', 0)} error / "
                f"{counts.get('warning', 0)} warning / {counts.get('info', 0)} info"
            ),
            "",
            "## 统计",
            "",
        ]
        for key, value in self.stats.items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## 发现", ""])
        if not self.findings:
            lines.append("未发现问题。")
        else:
            lines.extend(["| 级别 | 代码 | 位置 | 说明 |", "| --- | --- | --- | --- |"])
            for finding in self.findings:
                location = finding.path or ""
                if finding.pointer:
                    location = f"{location} {finding.pointer}".strip()
                message = finding.message.replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {finding.severity} | `{finding.code}` | {location} | {message} |"
                )
        lines.append("")
        lines.extend(["## 分类", "", "| 类别 | error | warning | info |", "| --- | --- | --- | --- |"])
        for category, bucket in sorted(self.categories().items()):
            lines.append(
                f"| {category} | {bucket.get('error', 0)} | "
                f"{bucket.get('warning', 0)} | {bucket.get('info', 0)} |"
            )
        if self.dependencies:
            lines.extend(["", "## 依赖图", "", "```mermaid", "graph TD"])
            for code, items in sorted(self.dependencies.items()):
                for dependency in items:
                    lines.append(f"  {dependency} --> {code}")
            lines.extend(["```", ""])
        return "\n".join(lines) + "\n"


def audit_agent(
    path: str | Path, *, out: str | Path | None = None
) -> tuple[AuditReport, list[Path]]:
    """Audit an agent project and write JSON + Markdown reports."""

    source = load_source(path)
    result, definition = validate_source(source)
    spec_dir = definition.spec_dir if definition else source.spec_dir
    config, config_diagnostics = load_audit_config(spec_dir)
    findings = [_from_diagnostic(diagnostic) for diagnostic in result.sorted()]
    fingerprint: str | None = None
    stats: dict = {}
    if definition is not None:
        stats = _stats(definition)
        findings.extend(_heuristic_findings(definition, config))
        findings.extend(
            run_custom_rules(
                definition, config, {finding.code for finding in findings}
            )
        )
        try:
            compiled = compile_agent(path)
            fingerprint = compiled.fingerprint
            stats["assets"] = len(compiled.assets)
            stats["asset_bytes"] = sum(asset.size for asset in compiled.assets)
        except Exception:  # noqa: BLE001, S110 - audit keeps validation findings
            pass
    findings = _apply_config(findings, config)
    findings.extend(_from_diagnostic(diagnostic) for diagnostic in config_diagnostics)
    root = definition.root if definition else source.root
    report = AuditReport(
        agent=definition.metadata.name if definition else "(unparsed)",
        root=str(root),
        fingerprint=fingerprint,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        findings=tuple(findings),
        stats=stats,
        dependencies={
            code: setting.depends_on
            for code, setting in config.rules.items()
            if setting.depends_on
        },
        conditions={
            code: {"when": list(setting.when), "unless": list(setting.unless)}
            for code, setting in config.rules.items()
            if setting.when or setting.unless
        },
    )
    directory = Path(out) if out else root / "reports" / "audit"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "audit.json"
    markdown_path = directory / "audit.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    return report, [json_path, markdown_path]


def _apply_config(
    findings: list[AuditFinding], config: AuditConfig
) -> list[AuditFinding]:
    filtered: list[AuditFinding] = []
    for finding in findings:
        if not config.enabled(finding.code):
            continue
        if config.ignored(finding.path):
            continue
        severity = config.severity(finding.code, finding.severity)
        filtered.append(
            AuditFinding(
                code=finding.code,
                severity=severity,
                message=finding.message,
                path=finding.path,
                pointer=finding.pointer,
                category=config.category(
                    finding.code, finding.category or _default_category(finding.code)
                ),
            )
        )
    return filtered


def _default_category(code: str) -> str:
    if code.startswith(("AUD00", "AUD01")):
        return "security"
    if code.startswith(("AUD1", "DTA4")):
        return "limits"
    if code.startswith("DTA3"):
        return "references"
    if code.startswith("DTA5"):
        return "projection"
    if code.startswith(("DTA1", "DTA2", "DTA6")):
        return "schema"
    return "general"


def _from_diagnostic(diagnostic: Diagnostic) -> AuditFinding:    return AuditFinding(
        code=diagnostic.code,
        severity=diagnostic.severity.value,
        message=diagnostic.message,
        path=diagnostic.path,
        pointer=diagnostic.pointer,
    )


def _stats(definition: AgentDefinition) -> dict[str, float | int]:
    counts = definition.entity_counts()
    knowledge_chars = sum(len(item.content) for item in definition.knowledge)
    stats: dict[str, float | int] = {
        key: value for key, value in counts.items() if value
    }
    stats["knowledge_chars"] = knowledge_chars
    limits = definition.effective_limits()
    stats["knowledge_budget_percent"] = round(
        100 * knowledge_chars / max(1, limits["knowledge_chars"]), 1
    )
    return stats


def _heuristic_findings(
    definition: AgentDefinition, config: AuditConfig
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    findings.extend(_secret_findings(definition, config))
    findings.extend(_tool_findings(definition, config))
    findings.extend(_model_findings(definition))
    findings.extend(_budget_findings(definition, config))
    return findings


def _secret_findings(
    definition: AgentDefinition, config: AuditConfig
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    patterns = list(_SECRET_PATTERNS)
    for raw_pattern in config.secret_patterns:
        try:
            patterns.append(re.compile(raw_pattern))
        except re.error:
            continue
    texts: list[tuple[str, str]] = []
    if definition.instructions:
        texts.append((definition.instructions.file or "AGENTS.md", definition.instructions.text))
    for knowledge in definition.knowledge:
        texts.append((knowledge.file, knowledge.content))
    for skill in definition.skills:
        texts.append((skill.file, skill.instructions))
    for name, server in definition.mcp:
        env = server.get("env") if isinstance(server, dict) else None
        if isinstance(env, dict):
            for key, value in env.items():
                if isinstance(value, str):
                    texts.append((f"mcp.json:{name}.env.{key}", value))
        headers = server.get("headers") if isinstance(server, dict) else None
        if isinstance(headers, dict):
            for key, value in headers.items():
                if isinstance(value, str):
                    texts.append((f"mcp.json:{name}.headers.{key}", value))
    for path, text in texts:
        for pattern in patterns:
            if pattern.search(text):
                findings.append(
                    AuditFinding(
                        "AUD001",
                        "warning",
                        f"{path} 中疑似包含明文密钥/令牌",
                        path=path,
                    )
                )
                break
    return findings


def _tool_findings(
    definition: AgentDefinition, config: AuditConfig
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    risky_names = config.risky_tool_names or _DEFAULT_RISKY_TOOL_NAMES
    for tool in definition.tools:
        annotations = dict(tool.annotations)
        risky = annotations.get("open_world") or annotations.get("destructive")
        name = tool.metadata.name
        if risky and not annotations.get("read_only"):
            findings.append(
                AuditFinding(
                    "AUD002",
                    "warning",
                    f"工具 {name!r} 具有外部/破坏性副作用，建议确认审批策略",
                    path=tool.file,
                )
            )
        if any(word in name for word in risky_names):
            findings.append(
                AuditFinding(
                    "AUD003",
                    "warning",
                    f"工具 {name!r} 名称暗示可执行命令，请确认权限边界",
                    path=tool.file,
                )
            )
        if not annotations:
            findings.append(
                AuditFinding(
                    "AUD004",
                    "info",
                    f"工具 {name!r} 未声明 annotations",
                    path=tool.file,
                )
            )
    return findings


def _model_findings(definition: AgentDefinition) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for model in definition.models:
        endpoint = dict(model.endpoint)
        base_url = endpoint.get("base_url", "")
        if isinstance(base_url, str) and base_url.startswith("http://"):
            findings.append(
                AuditFinding(
                    "AUD005",
                    "warning",
                    f"模型 {model.metadata.name!r} 使用明文 http:// 端点",
                    path=model.file,
                )
            )
        if model.model.startswith("your-") or model.model in {"latest", "TODO"}:
            findings.append(
                AuditFinding(
                    "AUD006",
                    "info",
                    f"模型 {model.metadata.name!r} 仍是占位符 {model.model!r}",
                    path=model.file,
                )
            )
    return findings


def _budget_findings(
    definition: AgentDefinition, config: AuditConfig
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    limits = definition.effective_limits()
    knowledge_chars = sum(len(item.content) for item in definition.knowledge)
    if knowledge_chars > config.budget_warning_ratio * limits["knowledge_chars"]:
        findings.append(
            AuditFinding(
                "AUD007",
                "warning",
                f"知识内容已使用 {knowledge_chars}/{limits['knowledge_chars']} 字符预算的"
                f" {int(config.budget_warning_ratio * 100)}% 以上",
                path="knowledge/",
            )
        )
    return findings


__all__ = ["AuditFinding", "AuditReport", "audit_agent"]
