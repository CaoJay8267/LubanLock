"""Agent layout conventions and resource-directory resolution.

Canonical layout (v0.2)::

    <project>/                 # agent project root
    ├── AGENTS.md              # instructions (agent-facing, open format)
    └── .agents/               # machine-readable definition
        ├── agent.yaml
        ├── knowledge/
        ├── skills/<name>/SKILL.md
        ├── agents/<name>.md
        ├── tools/ models/ workflows/ tasks/
        ├── mcp.json
        └── tests/

``.agent/`` (singular) is accepted as a deprecated alias for compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotagent.core.errors import LoadError

SPEC_DIR_NAMES = (".agents", ".agent")
CANONICAL_SPEC_DIR = ".agents"
AGENTS_FILE = "AGENTS.md"
LEGACY_INSTRUCTIONS_FILE = "instructions.md"
LEGACY_SUBAGENTS_DIR = "subagents"
RESOURCE_ROOT = "resource"
DEFAULT_RESOURCE_KIND = "agent"


@dataclass(frozen=True)
class AgentLayout:
    project_root: Path
    spec_dir: Path
    legacy_spec_dir: bool = False

    @property
    def spec_prefix(self) -> str:
        """POSIX path of the spec directory relative to the project root."""

        try:
            prefix = self.spec_dir.relative_to(self.project_root).as_posix()
        except ValueError:  # pragma: no cover - defensive
            prefix = self.spec_dir.name
        return "" if prefix == "." else prefix


def resolve_agent_layout(path: str | Path) -> AgentLayout:
    """Locate the project root and ``.agents`` spec directory for *path*."""

    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise LoadError(f"path does not exist: {candidate}")
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise LoadError(f"expected an agent directory, got file: {resolved}")
    if resolved.name in SPEC_DIR_NAMES:
        return AgentLayout(
            project_root=resolved.parent,
            spec_dir=resolved,
            legacy_spec_dir=resolved.name == ".agent",
        )
    for name in SPEC_DIR_NAMES:
        spec_dir = resolved / name
        if spec_dir.exists() and not spec_dir.is_dir():
            raise LoadError(f"{spec_dir} exists but is not a directory")
        if spec_dir.is_dir():
            return AgentLayout(
                project_root=resolved,
                spec_dir=resolved / name,
                legacy_spec_dir=name == ".agent",
            )
    if (resolved / "agent.yaml").is_file() or (resolved / "agent.yml").is_file():
        return AgentLayout(
            project_root=resolved.parent,
            spec_dir=resolved,
            legacy_spec_dir=True,
        )
    return AgentLayout(project_root=resolved, spec_dir=resolved)


def resolve_agent_root(path: str | Path) -> Path:
    """Return the project root for *path* (public compatibility helper)."""

    return resolve_agent_layout(path).project_root


def resource_agent_root(name: str, *, resource_root: str | Path = RESOURCE_ROOT) -> Path:
    return Path(resource_root) / DEFAULT_RESOURCE_KIND / name


def resolve_agent_target(
    value: str | Path, *, resource_root: str | Path = RESOURCE_ROOT
) -> Path:
    """Resolve a path or an agent name under ``resource/agent/``."""

    candidate = Path(value).expanduser()
    if candidate.exists():
        return candidate
    resource_candidate = resource_agent_root(str(value), resource_root=resource_root)
    if resource_candidate.exists():
        return resource_candidate
    raise LoadError(
        f"agent not found: {value} (checked the path and {resource_candidate})"
    )


__all__ = [
    "AGENTS_FILE",
    "CANONICAL_SPEC_DIR",
    "DEFAULT_RESOURCE_KIND",
    "LEGACY_INSTRUCTIONS_FILE",
    "LEGACY_SUBAGENTS_DIR",
    "RESOURCE_ROOT",
    "SPEC_DIR_NAMES",
    "AgentLayout",
    "resolve_agent_layout",
    "resolve_agent_root",
    "resolve_agent_target",
    "resource_agent_root",
]
