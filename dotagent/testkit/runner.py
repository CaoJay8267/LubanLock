"""Built-in declarative test suites and the runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dotagent.build.compiler import CompiledAgent, compile_agent
from dotagent.build.packaging import package_agent, verify_package
from dotagent.core.errors import Severity, TestRunError
from dotagent.core.graph import reachable
from dotagent.core.utils import pretty_json
from dotagent.runtime import build_plan

from .base import CaseResult, TestReport
from .checks import load_checks, run_checks

SUITES = ("workflow", "limits", "golden", "determinism", "packaging", "custom")


def run_tests(
    path: str | Path,
    *,
    update: bool = False,
    suites: tuple[str, ...] | None = None,
    filter_text: str | None = None,
) -> TestReport:
    """Compile the agent and run the selected declarative suites."""

    compiled = compile_agent(path)
    root = compiled.definition.root
    spec_dir = compiled.definition.spec_dir
    selected = list(suites) if suites else list(SUITES)
    unknown = [name for name in selected if name not in SUITES]
    if unknown:
        raise TestRunError(
            f"unknown test suite(s): {', '.join(unknown)}; "
            f"available: {', '.join(SUITES)}"
        )
    cases: list[CaseResult] = []
    for suite in selected:
        if suite == "workflow":
            cases.extend(_workflow_suite(compiled))
        elif suite == "limits":
            cases.extend(_limits_suite(compiled))
        elif suite == "golden":
            cases.extend(_golden_suite(compiled, spec_dir, update))
        elif suite == "determinism":
            cases.extend(_determinism_suite(compiled, root))
        elif suite == "packaging":
            cases.extend(_packaging_suite(compiled))
        elif suite == "custom":
            cases.extend(_custom_suite(spec_dir, compiled))
    if filter_text:
        needle = filter_text.lower()
        cases = [
            case
            for case in cases
            if needle in case.id.lower() or needle in case.title.lower()
        ]
    return TestReport(
        agent=compiled.definition.metadata.name,
        fingerprint=compiled.fingerprint,
        cases=tuple(cases),
    )


def _workflow_suite(compiled: CompiledAgent) -> list[CaseResult]:
    results: list[CaseResult] = []
    agent = compiled.definition
    for workflow in agent.workflows:
        name = workflow.metadata.name
        try:
            plan = build_plan(workflow)
        except ValueError as exc:
            results.append(
                CaseResult(
                    id=f"workflow/{name}",
                    suite="workflow",
                    title=f"Workflow {name} is acyclic",
                    status="failed",
                    message=str(exc),
                )
            )
            continue
        node_ids = [node.id for node in workflow.nodes]
        edges = [(edge.source, edge.target) for edge in workflow.edges]
        seen = reachable(workflow.entry, node_ids, edges)
        unreachable = sorted(set(node_ids) - seen)
        if unreachable:
            results.append(
                CaseResult(
                    id=f"workflow/{name}",
                    suite="workflow",
                    title=f"Workflow {name} is connected from its entry",
                    status="failed",
                    message="unreachable nodes: " + ", ".join(unreachable),
                )
            )
        else:
            results.append(
                CaseResult(
                    id=f"workflow/{name}",
                    suite="workflow",
                    title=f"Workflow {name} is acyclic and connected",
                    status="passed",
                    message=f"{len(plan.layers)} parallel layer(s)",
                )
            )
    return results


def _limits_suite(compiled: CompiledAgent) -> list[CaseResult]:
    agent = compiled.definition
    limits = agent.effective_limits()
    results = []

    knowledge_chars = sum(len(item.content) for item in agent.knowledge)
    results.append(
        _limit_case(
            "knowledge-chars",
            "Knowledge stays within its character budget",
            knowledge_chars,
            limits["knowledge_chars"],
        )
    )
    for key, count in (
        ("skill-count", len(agent.skills)),
        ("tool-count", len(agent.tools)),
        ("subagent-count", len(agent.subagents)),
    ):
        limit_key = key.replace("-", "_")
        results.append(
            _limit_case(
                key,
                f"{key.replace('-', ' ').title()} stays within budget",
                count,
                limits[limit_key],
            )
        )
    return results


def _limit_case(key: str, title: str, actual: int, limit: int) -> CaseResult:
    ok = actual <= limit
    return CaseResult(
        id=f"limits/{key}",
        suite="limits",
        title=title,
        status="passed" if ok else "failed",
        message=f"{actual} of {limit}",
    )


def _golden_suite(
    compiled: CompiledAgent, root: Path, update: bool
) -> list[CaseResult]:
    path = root / "tests" / "golden" / "agent.json"
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(compiled.to_json_text(), encoding="utf-8")
        return [
            CaseResult(
                id="golden/agent",
                suite="golden",
                title="Golden IR snapshot",
                status="passed",
                message=f"updated {path.relative_to(root).as_posix()}",
            )
        ]
    if not path.is_file():
        return [
            CaseResult(
                id="golden/agent",
                suite="golden",
                title="Golden IR snapshot",
                status="skipped",
                message="no snapshot yet; run 'dotagent test --update' to create one",
            )
        ]
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            CaseResult(
                id="golden/agent",
                suite="golden",
                title="Golden IR snapshot",
                status="failed",
                message=f"cannot read golden snapshot: {exc}",
            )
        ]
    actual = {
        "apiVersion": compiled.ir["apiVersion"],
        "agent": compiled.ir["agent"],
        "assets": compiled.ir["assets"],
    }
    wanted = {
        "apiVersion": expected.get("apiVersion"),
        "agent": expected.get("agent"),
        "assets": expected.get("assets"),
    }
    if actual == wanted:
        return [
            CaseResult(
                id="golden/agent",
                suite="golden",
                title="Golden IR snapshot",
                status="passed",
            )
        ]
    return [
        CaseResult(
            id="golden/agent",
            suite="golden",
            title="Golden IR snapshot",
            status="failed",
            message="compiled IR differs from the snapshot; review and run "
            "'dotagent test --update'",
        )
    ]


def _determinism_suite(compiled: CompiledAgent, root: Path) -> list[CaseResult]:
    again = compile_agent(root)
    problems: list[str] = []
    if again.fingerprint != compiled.fingerprint:
        problems.append("fingerprint changed between two compilations")
    if pretty_json(again.ir) != pretty_json(compiled.ir):
        problems.append("compiled IR changed between two compilations")
    if [(a.path, a.sha256) for a in again.assets] != [
        (a.path, a.sha256) for a in compiled.assets
    ]:
        problems.append("assets changed between two compilations")
    return [
        CaseResult(
            id="determinism/compile",
            suite="determinism",
            title="Compilation is deterministic",
            status="failed" if problems else "passed",
            message="; ".join(problems),
        )
    ]


def _packaging_suite(compiled: CompiledAgent) -> list[CaseResult]:
    cases: list[CaseResult] = []
    with tempfile.TemporaryDirectory() as directory:
        first = package_agent(compiled, Path(directory) / "first.zip")
        second = package_agent(compiled, Path(directory) / "second.zip")
        verification = verify_package(first)
        problems = list(verification.errors)
        if verification.fingerprint != compiled.fingerprint:
            problems.append("package fingerprint does not match the compiled agent")
        cases.append(
            CaseResult(
                id="packaging/verify",
                suite="packaging",
                title="Package round-trips and verifies",
                status="failed" if problems else "passed",
                message="; ".join(problems),
            )
        )
        same = first.read_bytes() == second.read_bytes()
        cases.append(
            CaseResult(
                id="packaging/reproducible",
                suite="packaging",
                title="Packages are byte-for-byte reproducible",
                status="passed" if same else "failed",
                message="" if same else "two packages of the same agent differ",
            )
        )
    return cases


def _custom_suite(root: Path, compiled: CompiledAgent) -> list[CaseResult]:
    checks, diagnostics = load_checks(root)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity is Severity.ERROR]
    if errors:
        return [
            CaseResult(
                id="custom/definition",
                suite="custom",
                title="Checks file is valid",
                status="failed",
                message="; ".join(diagnostic.format() for diagnostic in errors),
            )
        ]
    if not checks:
        return []
    return run_checks(checks, compiled.ir["agent"])
