"""Runtime adaptation without implementing an agent loop.

DotAgent deliberately stops at a declarative definition: a host (a harness, a
CLI, a service) decides how to call a model and how to execute tools.  This
module provides the pieces such a host needs:

* an :class:`AgentRuntime` view with prompt assembly and typed lookups,
* workflow execution plans (parallel layers, deterministic order),
* a :class:`RuntimeAdapter` registry for host-specific object models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

from dotagent.build.compiler import CompiledAgent, compile_agent
from dotagent.core.graph import layers
from dotagent.core.models import (
    Knowledge,
    Skill,
    SubAgent,
    Task,
    Tool,
    Workflow,
    WorkflowNode,
)
from dotagent.core.utils import canonical_json


@dataclass(frozen=True)
class WorkflowPlan:
    """A deterministic, layer-by-layer execution plan for one workflow."""

    name: str
    entry: str
    layers: tuple[tuple[str, ...], ...]
    nodes: tuple[WorkflowNode, ...]

    def node(self, node_id: str) -> WorkflowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entry": self.entry,
            "layers": [list(layer) for layer in self.layers],
            "nodes": [node.to_dict() for node in self.nodes],
        }


def build_plan(workflow: Workflow) -> WorkflowPlan:
    node_ids = tuple(node.id for node in workflow.nodes)
    edges = [(edge.source, edge.target) for edge in workflow.edges]
    grouped = layers(node_ids, edges)
    if grouped is None:  # pragma: no cover - validation rejects cycles
        raise ValueError(f"workflow {workflow.metadata.name!r} contains a cycle")
    return WorkflowPlan(
        name=workflow.metadata.name,
        entry=workflow.entry,
        layers=grouped,
        nodes=workflow.nodes,
    )


class AgentRuntime:
    """The view a host uses to execute a compiled agent definition."""

    def __init__(self, compiled: CompiledAgent):
        self.compiled = compiled
        self.definition = compiled.definition

    @classmethod
    def from_path(cls, path: str | Path) -> AgentRuntime:
        return cls(compile_agent(path))

    @property
    def fingerprint(self) -> str:
        return self.compiled.fingerprint

    def instructions(self) -> str:
        source = self.definition.instructions
        return source.text if source else ""

    def tools(self) -> tuple[Tool, ...]:
        return self.definition.tools

    def skills(self) -> tuple[Skill, ...]:
        return self.definition.skills

    def knowledge(self) -> tuple[Knowledge, ...]:
        return self.definition.knowledge

    def subagents(self) -> tuple[SubAgent, ...]:
        return self.definition.subagents

    def tasks(self) -> tuple[Task, ...]:
        return self.definition.tasks

    def workflows(self) -> tuple[Workflow, ...]:
        return self.definition.workflows

    def tool(self, name: str) -> Tool | None:
        return self.definition.tool(name)

    def skill(self, name: str) -> Skill | None:
        return self.definition.skill(name)

    def task(self, name: str) -> Task | None:
        return self.definition.task(name)

    def workflow(self, name: str) -> Workflow | None:
        return self.definition.workflow(name)

    def workflow_plan(self, name: str) -> WorkflowPlan:
        workflow = self.definition.workflow(name)
        if workflow is None:
            raise KeyError(f"unknown workflow {name!r}")
        return build_plan(workflow)

    def system_prompt(
        self,
        *,
        include_skills: bool = True,
        include_tools: bool = True,
        include_subagents: bool = True,
    ) -> str:
        """Assemble a deterministic system prompt from the definition."""

        metadata = self.definition.metadata
        lines = [f"# {metadata.title or metadata.name}", ""]
        if metadata.description:
            lines.extend([metadata.description, ""])
        if self.instructions().strip():
            lines.extend(["## Instructions", "", self.instructions().strip(), ""])
        if include_skills and self.skills():
            lines.extend(["## Skills", ""])
            for skill in self.skills():
                lines.append(f"- `{skill.name}`: {skill.description or ''}")
            lines.append("")
        if include_tools and self.tools():
            lines.extend(["## Tools", ""])
            for tool in self.tools():
                lines.append(f"- `{tool.metadata.name}`: {tool.metadata.description or ''}")
            lines.append("")
        if include_subagents and self.subagents():
            lines.extend(["## Subagents", ""])
            for subagent in self.subagents():
                lines.append(
                    f"- `{subagent.metadata.name}`: {subagent.metadata.description or ''}"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_dict(self) -> dict:
        return self.compiled.ir

    def to_json(self) -> str:
        return canonical_json(self.compiled.ir)


class RuntimeAdapter(ABC):
    """Translate a compiled agent into a host-specific object model."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def adapt(self, compiled: CompiledAgent):
        """Return the host representation."""

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description}


class DictAdapter(RuntimeAdapter):
    name = "dict"
    description = "Return the neutral compiled IR as a plain dictionary."

    def adapt(self, compiled: CompiledAgent) -> dict:
        return compiled.ir


_ADAPTERS: dict[str, RuntimeAdapter] = {"dict": DictAdapter()}
_EXTERNAL_LOADED = False


def register_adapter(adapter: RuntimeAdapter) -> None:
    _ADAPTERS[adapter.name] = adapter


def _load_external() -> None:
    global _EXTERNAL_LOADED
    if _EXTERNAL_LOADED:
        return
    _EXTERNAL_LOADED = True
    try:
        discovered = entry_points(group="dotagent.runtime_adapters")
    except Exception:  # noqa: BLE001  # pragma: no cover - importlib edge cases
        return
    for entry in discovered:
        loaded = entry.load()
        adapter = loaded() if isinstance(loaded, type) else loaded
        if isinstance(adapter, RuntimeAdapter) and adapter.name:
            _ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> RuntimeAdapter:
    _load_external()
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(_ADAPTERS))
        raise KeyError(f"unknown runtime adapter {name!r}; available: {available}") from exc


def list_adapters() -> list[dict]:
    _load_external()
    return [dict(adapter.to_dict(), source="builtin" if adapter.name == "dict" else "plugin") for _, adapter in sorted(_ADAPTERS.items())]


def adapt(compiled: CompiledAgent, name: str = "dict"):
    return get_adapter(name).adapt(compiled)


__all__ = [
    "AgentRuntime",
    "DictAdapter",
    "RuntimeAdapter",
    "WorkflowPlan",
    "adapt",
    "build_plan",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
