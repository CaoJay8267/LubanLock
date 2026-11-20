"""JSON Schema validation built into DotAgent."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from dotagent.core.errors import Diagnostic, Severity

from .validator import (
    SUPPORTED_KEYWORDS,
    SchemaResolutionError,
    SchemaStore,
    lint_json_schema,
    validate_instance,
)

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

AGENT_SCHEMA = "agent.schema.json"
SKILL_SCHEMA = "skill.schema.json"
KNOWLEDGE_SCHEMA = "knowledge.schema.json"
SUBAGENT_SCHEMA = "subagent.schema.json"
TOOL_SCHEMA = "tool.schema.json"
MODEL_SCHEMA = "model.schema.json"
WORKFLOW_SCHEMA = "workflow.schema.json"
TASK_SCHEMA = "task.schema.json"
CHECKS_SCHEMA = "checks.schema.json"
MCP_SCHEMA = "mcp.schema.json"


@lru_cache(maxsize=1)
def get_schema_store() -> SchemaStore:
    return SchemaStore(SCHEMA_DIR)


def schema_names() -> list[str]:
    return sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json"))


def load_schema(name: str) -> dict:
    return get_schema_store().load(name)


def validate_document(
    instance: object, schema_name: str, *, path: str | None = None
) -> list[Diagnostic]:
    """Validate *instance* against one of DotAgent's bundled schemas."""

    store = get_schema_store()
    schema_file = store.path_for(schema_name)
    schema = store.load(schema_name)
    diagnostics = validate_instance(
        instance,
        schema,
        store=store,
        base_file=schema_file,
    )
    if path:
        diagnostics = [
            replace(diagnostic, path=diagnostic.path or path)
            for diagnostic in diagnostics
        ]
    return diagnostics


def check_schema(name: str) -> list[Diagnostic]:
    """Check a bundled schema for unsupported keywords and broken ``$ref``s."""

    store = get_schema_store()
    schema_file = store.path_for(name)
    schema = store.load(name)
    diagnostics = lint_json_schema(schema, path=name, local_only=False)
    for ref in _iter_refs(schema):
        try:
            store.resolve(ref, schema_file)
        except SchemaResolutionError as exc:
            diagnostics.append(
                Diagnostic(
                    "DTA210",
                    f"cannot resolve $ref: {exc}",
                    Severity.ERROR,
                    path=name,
                )
            )
    return diagnostics


def _iter_refs(node: object):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_refs(value)


def check_all_schemas() -> dict[str, list[Diagnostic]]:
    return {name: check_schema(name) for name in schema_names()}


__all__ = [
    "AGENT_SCHEMA",
    "CHECKS_SCHEMA",
    "KNOWLEDGE_SCHEMA",
    "MCP_SCHEMA",
    "MODEL_SCHEMA",
    "SCHEMA_DIR",
    "SKILL_SCHEMA",
    "SUBAGENT_SCHEMA",
    "SUPPORTED_KEYWORDS",
    "TASK_SCHEMA",
    "TOOL_SCHEMA",
    "WORKFLOW_SCHEMA",
    "SchemaResolutionError",
    "SchemaStore",
    "check_all_schemas",
    "check_schema",
    "get_schema_store",
    "lint_json_schema",
    "load_schema",
    "schema_names",
    "validate_document",
]
