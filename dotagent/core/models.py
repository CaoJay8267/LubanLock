"""Typed, immutable in-memory representation of a loaded agent.

The models are deliberately plain dataclasses: they are cheap to construct,
easy to serialize deterministically and carry no runtime dependencies.  Every
``to_dict`` produces the canonical JSON shape used in compiled artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LIMITS: dict[str, int] = {
    "knowledge_chars": 100_000,
    "skill_count": 32,
    "tool_count": 64,
    "subagent_count": 16,
}


def _drop_empty(data: dict) -> dict:
    cleaned: dict = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, (tuple, list, dict)) and not value:
            continue
        cleaned[key] = value
    return cleaned


@dataclass(frozen=True)
class Metadata:
    name: str
    title: str | None = None
    description: str | None = None
    version: str = "0.1.0"
    labels: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "labels": dict(self.labels),
            "tags": list(self.tags),
        }
        return _drop_empty(data)


@dataclass(frozen=True)
class InstructionSource:
    text: str
    file: str | None = None

    def to_dict(self) -> dict:
        return _drop_empty({"text": self.text, "file": self.file})


@dataclass(frozen=True)
class Knowledge:
    metadata: Metadata
    content: str
    file: str
    priority: str = "normal"

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update({"content": self.content, "file": self.file, "priority": self.priority})
        return data


@dataclass(frozen=True)
class Skill:
    """An Agent Skills compliant skill.

    Only the fields defined by https://agentskills.io/specification are kept;
    DotAgent-specific bookkeeping lives outside ``SKILL.md``.
    """

    name: str
    description: str
    instructions: str
    file: str
    allowed_tools: tuple[str, ...] = ()
    license: str | None = None
    compatibility: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    files: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return _drop_empty(
            {
                "name": self.name,
                "description": self.description,
                "instructions": self.instructions,
                "file": self.file,
                "allowed_tools": list(self.allowed_tools),
                "license": self.license,
                "compatibility": self.compatibility,
                "metadata": dict(self.metadata),
                "files": list(self.files),
            }
        )


@dataclass(frozen=True)
class SubAgent:
    metadata: Metadata
    instructions: str
    file: str
    model: str | None = None
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "instructions": self.instructions,
                "file": self.file,
                "model": self.model,
                "tools": list(self.tools),
                "skills": list(self.skills),
                "agents": list(self.agents),
            }
        )
        return _drop_empty(data)


@dataclass(frozen=True)
class Tool:
    metadata: Metadata
    input_schema: dict
    file: str
    output_schema: dict | None = None
    annotations: tuple[tuple[str, bool], ...] = ()
    source: dict | None = None

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
                "annotations": dict(self.annotations),
                "source": self.source,
                "file": self.file,
            }
        )
        return _drop_empty(data)


@dataclass(frozen=True)
class Model:
    metadata: Metadata
    model: str
    file: str
    provider: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    capabilities: tuple[str, ...] = ()
    endpoint: tuple[tuple[str, str], ...] = ()
    parameters: tuple[tuple[str, float | int], ...] = ()
    provider_options: tuple[tuple[str, object], ...] = ()

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "model": self.model,
                "provider": self.provider,
                "context_window": self.context_window,
                "max_output_tokens": self.max_output_tokens,
                "capabilities": list(self.capabilities),
                "endpoint": dict(self.endpoint),
                "parameters": dict(self.parameters),
                "provider_options": dict(self.provider_options),
                "file": self.file,
            }
        )
        return _drop_empty(data)


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    kind: str
    ref: str | None = None
    title: str | None = None
    description: str | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return _drop_empty(
            {
                "id": self.id,
                "kind": self.kind,
                "ref": self.ref,
                "title": self.title,
                "description": self.description,
                "inputs": list(self.inputs),
                "outputs": list(self.outputs),
            }
        )


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str
    label: str | None = None

    def to_dict(self) -> dict:
        return _drop_empty({"from": self.source, "to": self.target, "label": self.label})


@dataclass(frozen=True)
class Workflow:
    metadata: Metadata
    entry: str
    file: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...] = ()

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "entry": self.entry,
                "nodes": [node.to_dict() for node in self.nodes],
                "edges": [edge.to_dict() for edge in self.edges],
                "file": self.file,
            }
        )
        return data

    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.id for node in self.nodes)

    def node(self, node_id: str) -> WorkflowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None


@dataclass(frozen=True)
class Task:
    metadata: Metadata
    file: str
    prompt: InstructionSource | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    workflow: str | None = None
    examples: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "prompt": self.prompt.to_dict() if self.prompt else None,
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
                "skills": list(self.skills),
                "tools": list(self.tools),
                "workflow": self.workflow,
                "examples": list(self.examples),
                "file": self.file,
            }
        )
        return _drop_empty(data)


@dataclass(frozen=True)
class Limits:
    knowledge_chars: int | None = None
    skill_count: int | None = None
    tool_count: int | None = None
    subagent_count: int | None = None

    def to_dict(self) -> dict:
        return _drop_empty(
            {
                "knowledge_chars": self.knowledge_chars,
                "skill_count": self.skill_count,
                "tool_count": self.tool_count,
                "subagent_count": self.subagent_count,
            }
        )


@dataclass(frozen=True)
class AgentDefinition:
    """One agent project: identity, entities and MCP servers."""

    metadata: Metadata
    root: Path = field(compare=False, repr=False)
    spec_dir: Path = field(compare=False, repr=False)
    instructions: InstructionSource | None = None
    model: str | None = None
    defaults: tuple[tuple[str, object], ...] = ()
    limits: Limits = field(default_factory=Limits)
    settings: tuple[tuple[str, object], ...] = ()
    uses: tuple[tuple[str, str | None], ...] = ()
    mcp: tuple[tuple[str, dict], ...] = ()
    knowledge: tuple[Knowledge, ...] = ()
    skills: tuple[Skill, ...] = ()
    subagents: tuple[SubAgent, ...] = ()
    tools: tuple[Tool, ...] = ()
    models: tuple[Model, ...] = ()
    workflows: tuple[Workflow, ...] = ()
    tasks: tuple[Task, ...] = ()
    files: tuple[str, ...] = ()

    # --- lookups -------------------------------------------------------
    @staticmethod
    def _name(item) -> str:
        name = getattr(item, "name", None)
        if isinstance(name, str):
            return name
        return item.metadata.name

    def _find(self, items, name: str):
        for item in items:
            if self._name(item) == name:
                return item
        return None

    def knowledge_by_name(self, name: str) -> Knowledge | None:
        return self._find(self.knowledge, name)

    def skill(self, name: str) -> Skill | None:
        return self._find(self.skills, name)

    def subagent(self, name: str) -> SubAgent | None:
        return self._find(self.subagents, name)

    def tool(self, name: str) -> Tool | None:
        return self._find(self.tools, name)

    def model_by_name(self, name: str) -> Model | None:
        return self._find(self.models, name)

    def workflow(self, name: str) -> Workflow | None:
        return self._find(self.workflows, name)

    def task(self, name: str) -> Task | None:
        return self._find(self.tasks, name)

    # --- limits --------------------------------------------------------
    def effective_limits(self) -> dict[str, int]:
        merged = dict(DEFAULT_LIMITS)
        for key, value in self.limits.to_dict().items():
            merged[key] = int(value)
        return merged

    def to_dict(self) -> dict:
        data = self.metadata.to_dict()
        data.update(
            {
                "instructions": self.instructions.to_dict() if self.instructions else None,
                "model": self.model,
                "defaults": dict(self.defaults),
                "limits": self.limits.to_dict(),
                "settings": dict(self.settings),
                "uses": [
                    _drop_empty({"path": path, "version": version})
                    for path, version in self.uses
                ],
                "mcp": {"mcpServers": dict(self.mcp)} if self.mcp else None,
                "knowledge": {item.metadata.name: item.to_dict() for item in self.knowledge},
                "skills": {item.name: item.to_dict() for item in self.skills},
                "subagents": {item.metadata.name: item.to_dict() for item in self.subagents},
                "tools": {item.metadata.name: item.to_dict() for item in self.tools},
                "models": {item.metadata.name: item.to_dict() for item in self.models},
                "workflows": {item.metadata.name: item.to_dict() for item in self.workflows},
                "tasks": {item.metadata.name: item.to_dict() for item in self.tasks},
                "files": list(self.files),
            }
        )
        return _drop_empty(data)

    def entity_counts(self) -> dict[str, int]:
        return {
            "knowledge": len(self.knowledge),
            "skills": len(self.skills),
            "subagents": len(self.subagents),
            "tools": len(self.tools),
            "models": len(self.models),
            "workflows": len(self.workflows),
            "tasks": len(self.tasks),
            "mcp_servers": len(self.mcp),
        }
