"""DotAgent: declarative, portable agent definitions."""

from __future__ import annotations

from pathlib import Path

from dotagent._version import __version__
from dotagent.audit import AuditReport, audit_agent, visualize_agent
from dotagent.build.compiler import (
    Asset,
    CompiledAgent,
    check_compiled,
    compile_agent,
    read_compiled_json,
    write_compiled,
)
from dotagent.build.lockfile import check_lock, read_lock, write_lock
from dotagent.build.packaging import (
    PackageVerification,
    package_agent,
    verify_package,
)
from dotagent.build.signing import generate_keypair
from dotagent.core.errors import (
    DotAgentError,
    LoadError,
    PackageError,
    ProjectionError,
    TestRunError,
    UsageError,
    ValidationFailed,
)
from dotagent.core.models import AgentDefinition
from dotagent.core.paths import (
    AgentLayout,
    resolve_agent_layout,
    resolve_agent_root,
    resolve_agent_target,
    resource_agent_root,
)
from dotagent.integrations import ImportReport, import_claude, import_package
from dotagent.project import (
    ProjectedFile,
    ProjectionResult,
    Projector,
    get_projector,
    list_projectors,
)
from dotagent.runtime import AgentRuntime, RuntimeAdapter, list_adapters
from dotagent.spec.loader import AgentSource, load_source
from dotagent.spec.schema import check_all_schemas, check_schema
from dotagent.spec.validator import validate_source
from dotagent.templates import init_agent
from dotagent.testkit import TestReport, run_tests


def load_agent(path: str | Path, *, strict: bool = False) -> AgentDefinition:
    """Load and validate an agent, raising on errors.

    ``strict=True`` also rejects warnings.
    """

    source = load_source(path)
    result, definition = validate_source(source)
    if definition is None:
        result.raise_if_errors("agent could not be loaded")
        raise LoadError("agent could not be loaded", diagnostics=result.sorted())
    result.raise_if_errors("agent validation failed")
    if strict and result.warnings:
        raise ValidationFailed(
            "agent validation produced warnings (strict mode)",
            diagnostics=result.sorted(),
        )
    return definition


def validate_agent(path: str | Path):
    """Validate an agent and return the full :class:`ValidationResult`."""

    source = load_source(path)
    result, _ = validate_source(source)
    return result


def project_agent(
    source: CompiledAgent | str | Path,
    target: str,
    out: str | Path | None = None,
) -> ProjectionResult:
    """Project a compiled agent (or a path to one) for *target*."""

    compiled = source if isinstance(source, CompiledAgent) else compile_agent(source)
    result = get_projector(target).project(compiled)
    if out is not None:
        result.write(out)
    return result


__all__ = [
    "AgentDefinition",
    "AgentLayout",
    "AgentRuntime",
    "AgentSource",
    "Asset",
    "AuditReport",
    "CompiledAgent",
    "DotAgentError",
    "ImportReport",
    "LoadError",
    "PackageError",
    "PackageVerification",
    "ProjectedFile",
    "ProjectionError",
    "ProjectionResult",
    "Projector",
    "RuntimeAdapter",
    "TestReport",
    "TestRunError",
    "UsageError",
    "ValidationFailed",
    "__version__",
    "audit_agent",
    "check_all_schemas",
    "check_compiled",
    "check_lock",
    "check_schema",
    "compile_agent",
    "generate_keypair",
    "get_projector",
    "import_claude",
    "import_package",
    "init_agent",
    "list_adapters",
    "list_projectors",
    "load_agent",
    "load_source",
    "package_agent",
    "project_agent",
    "read_compiled_json",
    "read_lock",
    "resolve_agent_layout",
    "resolve_agent_root",
    "resolve_agent_target",
    "resource_agent_root",
    "run_tests",
    "validate_agent",
    "validate_source",
    "verify_package",
    "visualize_agent",
    "write_compiled",
    "write_lock",
]
