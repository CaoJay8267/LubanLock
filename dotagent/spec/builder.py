"""Turn schema-valid raw documents into typed models."""

from __future__ import annotations

from pathlib import Path

from dotagent.core.errors import Diagnostic
from dotagent.core.models import (
    AgentDefinition,
    InstructionSource,
    Knowledge,
    Limits,
    Metadata,
    Model,
    Skill,
    SubAgent,
    Task,
    Tool,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from dotagent.core.utils import (
    UnsafePathError,
    read_text,
    resolve_relative,
    slugify,
)
from dotagent.spec.loader import AgentSource, RawEntity

KIND_LABEL = {
    "knowledge": "knowledge",
    "skill": "skill",
    "subagent": "subagent",
    "tool": "tool",
    "model": "model",
    "workflow": "workflow",
    "task": "task",
}


def build_agent(
    source: AgentSource,
    *,
    manifest: dict,
    entities: dict[str, list[RawEntity]],
) -> tuple[AgentDefinition | None, list[Diagnostic]]:
    """Build the typed definition.  Assumes schemas already passed."""

    diagnostics: list[Diagnostic] = []
    files = set(source.files)

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):  # pragma: no cover - schema guarantees this
        spec = {}

    instructions = _instruction_from_mapping(
        source.spec_dir,
        source.root,
        spec.get("instructions"),
        diagnostics,
        "/spec/instructions",
        files,
        path=source.manifest_rel,
        prefix=source.spec_prefix,
    )
    if instructions is None and source.instructions is not None:
        logical, text = source.instructions
        if text.strip():
            instructions = InstructionSource(text=text.strip(), file=logical)

    knowledge = _build_all(
        entities.get("knowledge", []),
        lambda raw: _build_knowledge(raw, diagnostics),
        "knowledge",
        diagnostics,
    )
    skills = _build_all(
        entities.get("skill", []),
        lambda raw: _build_skill(raw, diagnostics),
        "skill",
        diagnostics,
    )
    subagents = _build_all(
        entities.get("subagent", []),
        lambda raw: _build_subagent(raw, diagnostics),
        "subagent",
        diagnostics,
    )
    tools = _build_all(
        entities.get("tool", []),
        lambda raw: _build_tool(raw, diagnostics),
        "tool",
        diagnostics,
    )
    models = _build_all(
        entities.get("model", []),
        lambda raw: _build_model(raw, diagnostics),
        "model",
        diagnostics,
    )
    workflows = _build_all(
        entities.get("workflow", []),
        lambda raw: _build_workflow(raw, diagnostics),
        "workflow",
        diagnostics,
    )
    tasks = _build_all(
        entities.get("task", []),
        lambda raw: _build_task(raw, diagnostics, source, files),
        "task",
        diagnostics,
    )

    limits_raw = spec.get("limits") or {}
    limits = Limits(
        knowledge_chars=limits_raw.get("knowledge_chars"),
        skill_count=limits_raw.get("skill_count"),
        tool_count=limits_raw.get("tool_count"),
        subagent_count=limits_raw.get("subagent_count"),
    )
    uses = tuple(
        (str(item["path"]), item.get("version"))
        for item in spec.get("uses") or ()
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    )

    definition = AgentDefinition(
        metadata=_metadata_from_document(manifest),
        root=source.root,
        spec_dir=source.spec_dir,
        instructions=instructions,
        model=spec.get("model"),
        defaults=_items(spec.get("defaults")),
        limits=limits,
        settings=_items(spec.get("settings")),
        uses=uses,
        mcp=_mcp_servers(source.mcp),
        knowledge=knowledge,
        skills=skills,
        subagents=subagents,
        tools=tools,
        models=models,
        workflows=workflows,
        tasks=tasks,
        files=tuple(sorted(files)),
        library_roots=source.library_roots,
    )
    return definition, diagnostics


def _mcp_servers(value: object) -> tuple[tuple[str, dict], ...]:
    if not isinstance(value, dict):
        return ()
    servers = value.get("mcpServers")
    if not isinstance(servers, dict):
        return ()
    return tuple(
        sorted((str(name), config) for name, config in servers.items() if isinstance(config, dict))
    )


def _build_all(raws, builder, kind: str, diagnostics: list[Diagnostic]):
    seen: dict[str, str] = {}
    built = []
    for raw in raws:
        item = builder(raw)
        if item is None:
            continue
        name = item.name if isinstance(item, Skill) else item.metadata.name
        if name in seen:
            diagnostics.append(
                Diagnostic(
                    "DTA108",
                    f"duplicate {KIND_LABEL[kind]} name {name!r} "
                    f"(already defined in {seen[name]})",
                    path=raw.display,
                )
            )
            continue
        seen[name] = raw.display
        built.append(item)
    return tuple(sorted(built, key=lambda item: item.name if isinstance(item, Skill) else item.metadata.name))


def _metadata_from_document(document: dict) -> Metadata:
    meta = document.get("metadata") or {}
    if not isinstance(meta, dict):  # pragma: no cover - schema guarantees this
        meta = {}
    return Metadata(
        name=str(meta.get("name", "")),
        title=meta.get("title"),
        description=meta.get("description"),
        version=str(meta.get("version", "0.1.0")),
        labels=_labels(meta.get("labels")),
        tags=tuple(meta.get("tags") or ()),
    )


def _metadata_from_front_matter(data: dict, *, name: str) -> Metadata:
    return Metadata(
        name=name,
        title=data.get("title"),
        description=data.get("description"),
        version=str(data.get("version", "0.1.0")),
        labels=_labels(data.get("labels")),
        tags=tuple(data.get("tags") or ()),
    )


def _labels(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _items(value: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(value.items(), key=lambda item: item[0]))


def _build_knowledge(raw: RawEntity, diagnostics: list[Diagnostic]) -> Knowledge | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    explicit = data.get("name")
    if isinstance(explicit, str):
        name = explicit
    else:
        relative = Path(raw.rel)
        if relative.parts and relative.parts[0] == "libraries":
            relative = Path(*relative.parts[3:])
        if relative.parts and relative.parts[0] == "knowledge":
            relative = relative.relative_to("knowledge")
        name = slugify("-".join(relative.with_suffix("").parts))
    return Knowledge(
        metadata=_metadata_from_front_matter(data, name=name),
        content=raw.body or "",
        file=raw.rel,
        priority=str(data.get("priority", "normal")),
        source_root=raw.source_root,
    )


def _build_skill(raw: RawEntity, diagnostics: list[Diagnostic]) -> Skill | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    allowed = data.get("allowed-tools")
    allowed_tools = tuple(allowed.split()) if isinstance(allowed, str) else ()
    metadata = data.get("metadata")
    return Skill(
        name=name,
        description=description,
        instructions=raw.body or "",
        file=raw.rel,
        allowed_tools=allowed_tools,
        license=data.get("license"),
        compatibility=data.get("compatibility"),
        metadata=_labels(metadata),
        files=tuple(sorted(raw.extra_files)),
        source_root=raw.source_root,
    )


def _build_subagent(raw: RawEntity, diagnostics: list[Diagnostic]) -> SubAgent | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    agents = tuple(
        dict.fromkeys(
            [*tuple(data.get("agents") or ()), *tuple(data.get("uses") or ())]
        )
    )
    return SubAgent(
        metadata=_metadata_from_front_matter(data, name=name),
        instructions=raw.body or "",
        file=raw.rel,
        model=data.get("model"),
        tools=tuple(data.get("tools") or ()),
        skills=tuple(data.get("skills") or ()),
        agents=agents,
    )


def _build_tool(raw: RawEntity, diagnostics: list[Diagnostic]) -> Tool | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    metadata = _metadata_from_document(data)
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return None
    input_schema = spec.get("input_schema")
    if not isinstance(input_schema, dict):
        return None
    annotations = spec.get("annotations") or {}
    return Tool(
        metadata=metadata,
        input_schema=input_schema,
        file=raw.rel,
        output_schema=spec.get("output_schema"),
        annotations=tuple(sorted((str(key), bool(value)) for key, value in annotations.items())),
        source=spec.get("source"),
    )


def _build_model(raw: RawEntity, diagnostics: list[Diagnostic]) -> Model | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return None
    model = spec.get("model")
    if not isinstance(model, str) or not model:
        return None
    endpoint = spec.get("endpoint") or {}
    parameters = spec.get("parameters") or {}
    provider_options = spec.get("provider_options") or {}
    return Model(
        metadata=_metadata_from_document(data),
        model=model,
        file=raw.rel,
        provider=spec.get("provider"),
        context_window=spec.get("context_window"),
        max_output_tokens=spec.get("max_output_tokens"),
        capabilities=tuple(spec.get("capabilities") or ()),
        endpoint=tuple(sorted((str(k), str(v)) for k, v in endpoint.items())),
        parameters=tuple(sorted(parameters.items(), key=lambda item: item[0])),
        provider_options=tuple(sorted(provider_options.items(), key=lambda item: item[0])),
    )


def _build_workflow(raw: RawEntity, diagnostics: list[Diagnostic]) -> Workflow | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return None
    nodes_raw = spec.get("nodes")
    if not isinstance(nodes_raw, list):
        return None
    nodes: list[WorkflowNode] = []
    for node in nodes_raw:
        if not isinstance(node, dict):
            continue
        nodes.append(
            WorkflowNode(
                id=str(node.get("id", "")),
                kind=str(node.get("kind", "")),
                ref=node.get("ref"),
                title=node.get("title"),
                description=node.get("description"),
                inputs=tuple(node.get("inputs") or ()),
                outputs=tuple(node.get("outputs") or ()),
            )
        )
    edges: list[WorkflowEdge] = []
    for edge in spec.get("edges") or ():
        if not isinstance(edge, dict):
            continue
        edges.append(
            WorkflowEdge(
                source=str(edge.get("from", "")),
                target=str(edge.get("to", "")),
                label=edge.get("label"),
            )
        )
    entry = spec.get("entry")
    if not isinstance(entry, str):
        return None
    return Workflow(
        metadata=_metadata_from_document(data),
        entry=entry,
        file=raw.rel,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _build_task(
    raw: RawEntity,
    diagnostics: list[Diagnostic],
    source: AgentSource,
    files: set[str],
) -> Task | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return None
    prompt = _instruction_from_mapping(
        source.spec_dir,
        source.root,
        spec.get("prompt"),
        diagnostics,
        "/spec/prompt",
        files,
        path=raw.display,
        prefix=source.spec_prefix,
    )
    examples = spec.get("examples") or []
    return Task(
        metadata=_metadata_from_document(data),
        file=raw.rel,
        prompt=prompt,
        input_schema=spec.get("input_schema"),
        output_schema=spec.get("output_schema"),
        skills=tuple(spec.get("skills") or ()),
        tools=tuple(spec.get("tools") or ()),
        workflow=spec.get("workflow"),
        examples=tuple(example for example in examples if isinstance(example, dict)),
    )


def _instruction_from_mapping(
    spec_dir: Path,
    project_root: Path,
    value: object,
    diagnostics: list[Diagnostic],
    pointer: str,
    files: set[str],
    *,
    path: str,
    prefix: str,
) -> InstructionSource | None:
    if isinstance(value, str):
        return _read_instruction(spec_dir, project_root, value, diagnostics, pointer, files, path, prefix)
    if not isinstance(value, dict):
        return None
    if "text" in value and isinstance(value["text"], str):
        return InstructionSource(text=value["text"].strip())
    rel = value.get("file")
    if not isinstance(rel, str):
        return None
    return _read_instruction(spec_dir, project_root, rel, diagnostics, pointer, files, path, prefix)


def _read_instruction(
    spec_dir: Path,
    project_root: Path,
    relative: str,
    diagnostics: list[Diagnostic],
    pointer: str,
    files: set[str],
    path: str,
    prefix: str,
) -> InstructionSource | None:
    if relative == "AGENTS.md" and (project_root / relative).is_file():
        text = _read_text(project_root / relative, relative, diagnostics, pointer, path)
        if text is None:
            return None
        files.add("AGENTS.md")
        return InstructionSource(text=text.strip(), file="AGENTS.md")
    text = _read_declared_file(spec_dir, relative, diagnostics, pointer, path)
    if text is None:
        return None
    files.add(f"{prefix}/{relative}" if prefix else relative)
    return InstructionSource(text=text.strip(), file=relative)


def _read_text(
    path: Path,
    display: str,
    diagnostics: list[Diagnostic],
    pointer: str,
    source_path: str,
) -> str | None:
    try:
        return read_text(path)
    except ValueError as exc:
        diagnostics.append(Diagnostic("DTA105", str(exc), path=source_path, pointer=pointer))
    except UnicodeDecodeError as exc:
        diagnostics.append(
            Diagnostic("DTA106", f"file is not valid UTF-8: {exc}", path=source_path, pointer=pointer)
        )
    except OSError as exc:
        diagnostics.append(Diagnostic("DTA102", f"cannot read file: {exc}", path=source_path, pointer=pointer))
    return None


def _read_declared_file(
    spec_dir: Path,
    relative: str,
    diagnostics: list[Diagnostic],
    pointer: str,
    path: str,
) -> str | None:
    try:
        resolved = resolve_relative(spec_dir, relative, field=pointer)
    except UnsafePathError as exc:
        diagnostics.append(
            Diagnostic(
                "DTA109",
                str(exc),
                path=path,
                pointer=pointer,
                hint="paths must stay inside the .agents directory",
            )
        )
        return None
    if not resolved.is_file():
        diagnostics.append(
            Diagnostic("DTA102", f"declared file does not exist: {relative}", path=path, pointer=pointer)
        )
        return None
    try:
        return read_text(resolved)
    except ValueError as exc:
        diagnostics.append(Diagnostic("DTA105", str(exc), path=path, pointer=pointer))
    except UnicodeDecodeError as exc:
        diagnostics.append(
            Diagnostic("DTA106", f"file is not valid UTF-8: {exc}", path=path, pointer=pointer)
        )
    except OSError as exc:
        diagnostics.append(Diagnostic("DTA102", f"cannot read file: {exc}", path=path, pointer=pointer))
    return None
