"""Schema and semantic validation for an agent project."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from lubanlock.core.errors import Diagnostic, Severity, ValidationResult
from lubanlock.core.graph import reachable as graph_reachable
from lubanlock.core.graph import topological_order
from lubanlock.core.models import AgentDefinition, Workflow
from lubanlock.spec.builder import build_agent
from lubanlock.spec.inheritance import resolve_all_entities
from lubanlock.spec.libraries import attach_libraries
from lubanlock.spec.loader import AgentSource, RawEntity
from lubanlock.spec.schema import (
    AGENT_SCHEMA,
    MCP_SCHEMA,
    SCHEMA_DIR,
    get_schema_store,
    lint_json_schema,
    validate_document,
)
from lubanlock.spec.schema.validator import validate_instance

SUPPORTED_API_VERSION = "lubanlock/v1alpha1"

_NODE_REF_KINDS = {
    "task": "tasks",
    "subagent": "subagents",
    "tool": "tools",
    "workflow": "workflows",
}


def validate_source(source: AgentSource) -> tuple[ValidationResult, AgentDefinition | None]:
    """Validate a loaded source tree.  Never raises for content problems."""

    result = ValidationResult()
    result.extend(source.diagnostics)

    manifest, manifest_valid = _validate_manifest(source, result)
    if source.mcp is not None:
        result.extend(
            validate_document(
                source.mcp, MCP_SCHEMA, path=source.mcp_rel or "mcp.json"
            )
        )
    if manifest_valid:
        attach_libraries(source, manifest, result.diagnostics)
    resolved_entities, inheritance_diagnostics = resolve_all_entities(
        source.entities
    )
    result.extend(inheritance_diagnostics)
    valid_entities = _validate_entities(source, result, resolved_entities)
    if not manifest_valid:
        return result, None

    definition, build_diagnostics = build_agent(
        source, manifest=manifest, entities=valid_entities
    )
    result.extend(build_diagnostics)
    if definition is None:  # pragma: no cover - manifest was valid
        return result, None

    _domain_checks(definition, result)
    return result, definition


def _display(agent: AgentDefinition, relative: str) -> str:
    try:
        prefix = agent.spec_dir.relative_to(agent.root).as_posix()
    except ValueError:  # pragma: no cover - defensive
        prefix = agent.spec_dir.name
    return f"{prefix}/{relative}" if prefix else relative


def _validate_manifest(
    source: AgentSource, result: ValidationResult
) -> tuple[dict, bool]:
    data = source.manifest
    if data is None:
        return {}, False
    if not isinstance(data, dict):
        result.extend(validate_document(data, AGENT_SCHEMA, path=source.manifest_rel))
        return {}, False
    if "apiVersion" in data and data["apiVersion"] != SUPPORTED_API_VERSION:
        result.error(
            "DTA107",
            f"unsupported apiVersion {data['apiVersion']!r}; "
            f"this LubanLock supports {SUPPORTED_API_VERSION}",
            path=source.manifest_rel,
        )
        return data, False
    if "kind" in data and data["kind"] != "Agent":
        result.error(
            "DTA111",
            f"expected kind 'Agent', got {data['kind']!r}",
            path=source.manifest_rel,
        )
        return data, False
    diagnostics = validate_document(data, AGENT_SCHEMA, path=source.manifest_rel)
    result.extend(diagnostics)
    return data, not any(d.severity is Severity.ERROR for d in diagnostics)


def _validate_entities(
    source: AgentSource,
    result: ValidationResult,
    entities: dict[str, list[RawEntity]],
) -> dict[str, list[RawEntity]]:
    valid: dict[str, list[RawEntity]] = {kind: [] for kind in entities}
    for kind, raws in entities.items():
        for raw in raws:
            diagnostics = validate_document(raw.data, raw.schema, path=raw.display)
            result.extend(diagnostics)
            if not any(d.severity is Severity.ERROR for d in diagnostics):
                valid[kind].append(raw)
    return valid


def _domain_checks(agent: AgentDefinition, result: ValidationResult) -> None:
    _check_agent(agent, result)
    _check_skills(agent, result)
    _check_subagents(agent, result)
    _check_tools(agent, result)
    _check_mcp(agent, result)
    _check_tasks(agent, result)
    _check_workflows(agent, result)
    _check_limits(agent, result)


def _check_agent(agent: AgentDefinition, result: ValidationResult) -> None:
    manifest = _display(agent, "agent.yaml")
    if not agent.metadata.description:
        result.warning(
            "DTA901",
            "agent has no description; hosts and models use it to route work",
            path=manifest,
            pointer="/metadata/description",
        )
    if agent.instructions is None:
        result.warning(
            "DTA905",
            "agent has no instructions; add AGENTS.md at the project root",
            path=manifest,
            pointer="/spec/instructions",
        )
    elif not agent.instructions.text.strip():
        result.warning(
            "DTA905",
            "agent instructions are empty",
            path=agent.instructions.file or "AGENTS.md",
        )
    if agent.model and agent.model_by_name(agent.model) is None:
        result.error(
            "DTA301",
            f"unknown model {agent.model!r}",
            path=manifest,
            pointer="/spec/model",
        )


def _check_skills(agent: AgentDefinition, result: ValidationResult) -> None:
    tool_names = {tool.metadata.name for tool in agent.tools}
    for skill in agent.skills:
        path = _display(agent, skill.file)
        pointer = f"/skills/{skill.name}"
        if not skill.instructions.strip():
            result.warning(
                "DTA902",
                f"skill {skill.name!r} has no instructions",
                path=path,
                pointer=pointer,
            )
        parent = Path(skill.file).parent.name
        if parent != skill.name:
            result.error(
                "DTA908",
                f"skill name {skill.name!r} must match its parent directory {parent!r}",
                path=path,
                pointer=f"{pointer}/name",
            )
        for index, tool_name in enumerate(skill.allowed_tools):
            if tool_name not in tool_names:
                result.error(
                    "DTA301",
                    f"skill {skill.name!r} references unknown tool {tool_name!r}",
                    path=path,
                    pointer=f"{pointer}/allowed-tools/{index}",
                )


def _check_subagents(agent: AgentDefinition, result: ValidationResult) -> None:
    tool_names = {tool.metadata.name for tool in agent.tools}
    skill_names = {skill.name for skill in agent.skills}
    model_names = {model.metadata.name for model in agent.models}
    subagent_names = {subagent.metadata.name for subagent in agent.subagents}
    for subagent in agent.subagents:
        path = _display(agent, subagent.file)
        pointer = f"/agents/{subagent.metadata.name}"
        if not subagent.instructions.strip():
            result.warning(
                "DTA902",
                f"subagent {subagent.metadata.name!r} has no instructions",
                path=path,
                pointer=pointer,
            )
        if subagent.model and subagent.model not in model_names:
            result.error(
                "DTA301",
                f"subagent {subagent.metadata.name!r} references unknown model {subagent.model!r}",
                path=path,
                pointer=f"{pointer}/model",
            )
        for index, tool_name in enumerate(subagent.tools):
            if tool_name not in tool_names:
                result.error(
                    "DTA301",
                    f"subagent {subagent.metadata.name!r} references unknown tool {tool_name!r}",
                    path=path,
                    pointer=f"{pointer}/tools/{index}",
                )
        for index, skill_name in enumerate(subagent.skills):
            if skill_name not in skill_names:
                result.error(
                    "DTA301",
                    f"subagent {subagent.metadata.name!r} references unknown skill {skill_name!r}",
                    path=path,
                    pointer=f"{pointer}/skills/{index}",
                )
        for index, child_name in enumerate(subagent.agents):
            if child_name not in subagent_names:
                result.error(
                    "DTA301",
                    f"subagent {subagent.metadata.name!r} references unknown child agent {child_name!r}",
                    path=path,
                    pointer=f"{pointer}/agents/{index}",
                )
    _check_subagent_graph(agent, result)

def _check_subagent_graph(agent: AgentDefinition, result: ValidationResult) -> None:
    by_name = {subagent.metadata.name: subagent for subagent in agent.subagents}
    reported_cycles: set[tuple[str, ...]] = set()

    def depth(name: str, stack: tuple[str, ...] = ()) -> int:
        subagent = by_name.get(name)
        if subagent is None:
            return 0
        if name in stack:
            cycle = stack[stack.index(name) :] + (name,)
            if cycle not in reported_cycles:
                reported_cycles.add(cycle)
                result.error(
                    "DTA323",
                    "nested subagent cycle: " + " -> ".join(cycle),
                    path=_display(agent, subagent.file),
                    pointer=f"/agents/{name}",
                )
            return 0
        best = 0
        for child in subagent.agents:
            best = max(best, 1 + depth(child, (*stack, name)))
        return best

    for subagent in agent.subagents:
        if depth(subagent.metadata.name) > 8:
            result.error(
                "DTA324",
                f"nested subagent depth exceeds 8 at {subagent.metadata.name!r}",
                path=_display(agent, subagent.file),
                pointer=f"/agents/{subagent.metadata.name}",
            )


def _check_tools(agent: AgentDefinition, result: ValidationResult) -> None:
    for tool in agent.tools:
        path = _display(agent, tool.file)
        pointer = f"/tools/{tool.metadata.name}"
        _check_object_schema(
            tool.input_schema,
            result,
            path=path,
            pointer=f"{pointer}/spec/input_schema",
            what=f"tool {tool.metadata.name!r} input schema",
        )
        if tool.output_schema is not None:
            result.extend(
                _prefix(
                    lint_json_schema(tool.output_schema, path=path),
                    f"{pointer}/spec/output_schema",
                    path,
                )
            )
        annotations = dict(tool.annotations)
        if annotations.get("read_only") and annotations.get("destructive"):
            result.warning(
                "DTA909",
                f"tool {tool.metadata.name!r} is both read_only and destructive",
                path=path,
                pointer=f"{pointer}/spec/annotations",
            )


def _check_mcp(agent: AgentDefinition, result: ValidationResult) -> None:
    servers = {name: config for name, config in agent.mcp}
    if not servers:
        mcp_tools = [
            tool.metadata.name
            for tool in agent.tools
            if isinstance(tool.source, dict) and tool.source.get("kind") == "mcp"
        ]
        if mcp_tools:
            result.warning(
                "DTA914",
                "tools reference MCP servers but no .agents/mcp.json is declared: "
                + ", ".join(sorted(mcp_tools)),
                path=_display(agent, "mcp.json"),
            )
        return
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        if "command" not in config and "url" not in config:
            result.warning(
                "DTA912",
                f"MCP server {name!r} declares neither 'command' nor 'url'",
                path=_display(agent, "mcp.json"),
                pointer=f"/mcpServers/{name}",
            )
        url = config.get("url")
        if isinstance(url, str) and url.startswith("http://"):
            result.warning(
                "DTA913",
                f"MCP server {name!r} uses an insecure http:// URL",
                path=_display(agent, "mcp.json"),
                pointer=f"/mcpServers/{name}/url",
            )
    for tool in agent.tools:
        source = tool.source
        if not isinstance(source, dict) or source.get("kind") != "mcp":
            continue
        ref = source.get("ref")
        if not isinstance(ref, str) or "/" not in ref:
            continue
        server = ref.split("/", 1)[0]
        if server not in servers:
            result.warning(
                "DTA914",
                f"tool {tool.metadata.name!r} references undeclared MCP server {server!r}",
                path=_display(agent, tool.file),
                pointer=f"/tools/{tool.metadata.name}/spec/source/ref",
            )


def _check_tasks(agent: AgentDefinition, result: ValidationResult) -> None:
    tool_names = {tool.metadata.name for tool in agent.tools}
    skill_names = {skill.name for skill in agent.skills}
    workflow_names = {workflow.metadata.name for workflow in agent.workflows}
    store = get_schema_store()
    inline = SCHEMA_DIR / "_inline.schema.json"
    for task in agent.tasks:
        path = _display(agent, task.file)
        pointer = f"/tasks/{task.metadata.name}"
        if task.prompt is None and task.workflow is None:
            result.warning(
                "DTA907",
                f"task {task.metadata.name!r} has neither a prompt nor a workflow",
                path=path,
                pointer=pointer,
            )
        if task.prompt is not None and not task.prompt.text.strip():
            result.warning(
                "DTA902",
                f"task {task.metadata.name!r} prompt is empty",
                path=path,
                pointer=f"{pointer}/spec/prompt",
            )
        for index, tool_name in enumerate(task.tools):
            if tool_name not in tool_names:
                result.error(
                    "DTA301",
                    f"task {task.metadata.name!r} references unknown tool {tool_name!r}",
                    path=path,
                    pointer=f"{pointer}/tools/{index}",
                )
        for index, skill_name in enumerate(task.skills):
            if skill_name not in skill_names:
                result.error(
                    "DTA301",
                    f"task {task.metadata.name!r} references unknown skill {skill_name!r}",
                    path=path,
                    pointer=f"{pointer}/skills/{index}",
                )
        if task.workflow and task.workflow not in workflow_names:
            result.error(
                "DTA301",
                f"task {task.metadata.name!r} references unknown workflow {task.workflow!r}",
                path=path,
                pointer=f"{pointer}/workflow",
            )
        if task.input_schema is not None:
            _check_object_schema(
                task.input_schema,
                result,
                path=path,
                pointer=f"{pointer}/spec/input_schema",
                what=f"task {task.metadata.name!r} input schema",
            )
            for index, example in enumerate(task.examples):
                if "input" not in example:
                    continue
                example_pointer = f"{pointer}/spec/examples/{index}/input"
                diagnostics = validate_instance(
                    example["input"],
                    task.input_schema,
                    store=store,
                    base_file=inline,
                    pointer=example_pointer,
                )
                result.extend(
                    [
                        replace(diagnostic, path=diagnostic.path or path)
                        for diagnostic in diagnostics
                    ]
                )
        if task.output_schema is not None:
            result.extend(
                _prefix(
                    lint_json_schema(task.output_schema, path=path),
                    f"{pointer}/spec/output_schema",
                    path,
                )
            )


def _check_object_schema(
    schema: dict,
    result: ValidationResult,
    *,
    path: str,
    pointer: str,
    what: str,
) -> None:
    if not _schema_is_object(schema):
        result.error(
            "DTA308",
            f"{what} must declare \"type\": \"object\"",
            path=path,
            pointer=pointer,
        )
    result.extend(_prefix(lint_json_schema(schema, path=path), pointer, path))
    root_required = schema.get("required") or []
    properties = schema.get("properties") or {}
    if (
        isinstance(root_required, list)
        and schema.get("additionalProperties") is False
        and isinstance(properties, dict)
    ):
        for index, name in enumerate(root_required):
            if isinstance(name, str) and name not in properties:
                result.error(
                    "DTA308",
                    f"required property {name!r} is not declared in properties and "
                    "additionalProperties is false",
                    path=path,
                    pointer=f"{pointer}/required/{index}",
                )


def _schema_is_object(schema: dict) -> bool:
    declared = schema.get("type")
    return declared == "object" or (
        isinstance(declared, list) and "object" in declared
    )


def _prefix(
    diagnostics: list[Diagnostic], base_pointer: str, path: str
) -> list[Diagnostic]:
    return [
        replace(
            diagnostic,
            pointer=f"{base_pointer}{diagnostic.pointer}",
            path=diagnostic.path or path,
        )
        for diagnostic in diagnostics
    ]


def _check_workflows(agent: AgentDefinition, result: ValidationResult) -> None:
    names = {
        "task": {task.metadata.name for task in agent.tasks},
        "subagent": {subagent.metadata.name for subagent in agent.subagents},
        "tool": {tool.metadata.name for tool in agent.tools},
        "workflow": {workflow.metadata.name for workflow in agent.workflows},
    }
    for workflow in agent.workflows:
        _check_single_workflow(agent, workflow, names, result)
    _check_workflow_cycles(agent, result)


def _check_single_workflow(
    agent: AgentDefinition,
    workflow: Workflow,
    names: dict[str, set[str]],
    result: ValidationResult,
) -> None:
    path = _display(agent, workflow.file)
    base = f"/workflows/{workflow.metadata.name}/spec"
    seen: set[str] = set()
    for index, node in enumerate(workflow.nodes):
        if node.id in seen:
            result.error(
                "DTA302",
                f"duplicate node id {node.id!r} in workflow {workflow.metadata.name!r}",
                path=path,
                pointer=f"{base}/nodes/{index}/id",
            )
        seen.add(node.id)
    for index, edge in enumerate(workflow.edges):
        for endpoint in (edge.source, edge.target):
            if endpoint not in seen:
                result.error(
                    "DTA306",
                    f"edge references unknown node {endpoint!r}",
                    path=path,
                    pointer=f"{base}/edges/{index}",
                )
    if workflow.entry not in seen:
        result.error(
            "DTA305",
            f"entry node {workflow.entry!r} does not exist in workflow "
            f"{workflow.metadata.name!r}",
            path=path,
            pointer=f"{base}/entry",
        )
    incoming = {edge.target for edge in workflow.edges}
    for index, node in enumerate(workflow.nodes):
        pointer = f"{base}/nodes/{index}"
        if node.kind == "agent":
            if node.ref is not None and node.ref != agent.metadata.name:
                result.error(
                    "DTA303",
                    f"agent node must not reference another agent, got {node.ref!r}",
                    path=path,
                    pointer=f"{pointer}/ref",
                )
        elif node.kind in {"human", "join"}:
            if node.ref is not None:
                result.error(
                    "DTA303",
                    f"node kind {node.kind!r} does not take a ref",
                    path=path,
                    pointer=f"{pointer}/ref",
                )
            if node.kind == "join" and node.id not in incoming:
                result.error(
                    "DTA306",
                    f"join node {node.id!r} has no incoming edges",
                    path=path,
                    pointer=pointer,
                )
        elif node.kind in _NODE_REF_KINDS:
            if not node.ref:
                result.error(
                    "DTA303",
                    f"node kind {node.kind!r} requires a ref",
                    path=path,
                    pointer=pointer,
                )
            elif node.ref not in names[node.kind]:
                result.error(
                    "DTA301",
                    f"node {node.id!r} references unknown {node.kind} {node.ref!r}",
                    path=path,
                    pointer=f"{pointer}/ref",
                )

    order = _topological_order(workflow)
    if order is None:
        result.error(
            "DTA304",
            f"workflow {workflow.metadata.name!r} contains a cycle",
            path=path,
        )
        return
    reachable = _reachable(workflow)
    unreachable = sorted(set(seen) - reachable)
    if unreachable:
        result.warning(
            "DTA307",
            f"unreachable nodes in workflow {workflow.metadata.name!r}: "
            + ", ".join(unreachable),
            path=path,
        )


def _topological_order(workflow: Workflow) -> list[str] | None:
    return topological_order(
        workflow.node_ids(),
        [(edge.source, edge.target) for edge in workflow.edges],
    )


def _reachable(workflow: Workflow) -> set[str]:
    return graph_reachable(
        workflow.entry,
        workflow.node_ids(),
        [(edge.source, edge.target) for edge in workflow.edges],
    )


def _check_workflow_cycles(agent: AgentDefinition, result: ValidationResult) -> None:
    graphs: dict[str, list[str]] = {}
    for workflow in agent.workflows:
        graphs[workflow.metadata.name] = sorted(
            node.ref
            for node in workflow.nodes
            if node.kind == "workflow" and node.ref is not None
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            workflow = next(
                (w for w in agent.workflows if w.metadata.name == name), None
            )
            result.error(
                "DTA304",
                f"workflow call cycle detected at {name!r}",
                path=_display(agent, workflow.file) if workflow else None,
            )
            return
        visiting.add(name)
        for target in graphs.get(name, []):
            if target in graphs:
                visit(target)
        visiting.discard(name)
        visited.add(name)

    for name in sorted(graphs):
        visit(name)


def _check_limits(agent: AgentDefinition, result: ValidationResult) -> None:
    limits = agent.effective_limits()
    knowledge_chars = sum(len(item.content) for item in agent.knowledge)
    if knowledge_chars > limits["knowledge_chars"]:
        result.warning(
            "DTA401",
            f"knowledge content is {knowledge_chars} characters, "
            f"limit is {limits['knowledge_chars']}",
            path=_display(agent, "knowledge") + "/",
            hint="trim knowledge or raise spec.limits.knowledge_chars",
        )
    counts = {
        "skill_count": len(agent.skills),
        "tool_count": len(agent.tools),
        "subagent_count": len(agent.subagents),
    }
    for key, count in counts.items():
        if count > limits[key]:
            result.warning(
                "DTA401",
                f"{key.replace('_count', '')} count is {count}, limit is {limits[key]}",
                path=_display(agent, "agent.yaml"),
                pointer="/spec/limits",
            )
