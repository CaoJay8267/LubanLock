"""LubanLock: declarative, portable agent definitions."""

from __future__ import annotations

from pathlib import Path

from lubanlock._version import __version__
from lubanlock.audit import (
    AuditConfig,
    AuditReport,
    AuditRule,
    audit_agent,
    register_audit_rule,
    visualize_agent,
)
from lubanlock.build.compiler import (
    Asset,
    CompiledAgent,
    check_compiled,
    compile_agent,
    read_compiled_json,
    write_compiled,
)
from lubanlock.build.lockfile import check_lock, read_lock, write_lock
from lubanlock.build.packaging import (
    PackageVerification,
    package_agent,
    verify_package,
)
from lubanlock.build.signing import (
    generate_certificate_chain,
    generate_keypair,
    rotate_signing_certificate,
)
from lubanlock.core.errors import (
    LoadError,
    LubanLockError,
    PackageError,
    ProjectionError,
    TestRunError,
    UsageError,
    ValidationFailed,
)
from lubanlock.core.models import AgentDefinition
from lubanlock.core.paths import (
    AgentLayout,
    resolve_agent_layout,
    resolve_agent_root,
    resolve_agent_target,
    resource_agent_root,
)
from lubanlock.integrations import (
    DshImportReport,
    ImportReport,
    McpDiscovery,
    McpImportReport,
    OpenAIImportReport,
    discover_agent,
    discover_server,
    import_claude,
    import_copilot,
    import_dsh,
    import_openai,
    import_package,
    import_tools,
    looks_like_copilot,
    looks_like_dsh,
    looks_like_openai,
)
from lubanlock.project import (
    ProjectedFile,
    ProjectionResult,
    Projector,
    get_projector,
    list_projectors,
)
from lubanlock.runtime import AgentRuntime, RuntimeAdapter, list_adapters
from lubanlock.spec.loader import AgentSource, load_source
from lubanlock.spec.schema import check_all_schemas, check_schema
from lubanlock.spec.validator import validate_source
from lubanlock.templates import init_agent
from lubanlock.testkit import TestReport, run_tests


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
    "AuditConfig",
    "AuditReport",
    "AuditRule",
    "CompiledAgent",
    "DshImportReport",
    "ImportReport",
    "LoadError",
    "LubanLockError",
    "McpDiscovery",
    "McpImportReport",
    "OpenAIImportReport",
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
    "discover_agent",
    "discover_server",
    "generate_certificate_chain",
    "generate_keypair",
    "get_projector",
    "import_claude",
    "import_copilot",
    "import_dsh",
    "import_openai",
    "import_package",
    "import_tools",
    "init_agent",
    "list_adapters",
    "list_projectors",
    "load_agent",
    "load_source",
    "looks_like_copilot",
    "looks_like_dsh",
    "looks_like_openai",
    "package_agent",
    "project_agent",
    "read_compiled_json",
    "read_lock",
    "register_audit_rule",
    "resolve_agent_layout",
    "resolve_agent_root",
    "resolve_agent_target",
    "resource_agent_root",
    "rotate_signing_certificate",
    "run_tests",
    "validate_agent",
    "validate_source",
    "verify_package",
    "visualize_agent",
    "write_compiled",
    "write_lock",
]
