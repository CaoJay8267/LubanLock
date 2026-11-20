"""Import a Claude Code project into a DotAgent project.

Supported inputs (any combination): ``CLAUDE.md``, ``.claude/agents/*.md``,
``.claude/skills/*/SKILL.md``, ``.claude/commands/**/*.md`` and
``.mcp.json``.  The importer is lossy by design and reports what it cannot map.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from dotagent.build.packaging import read_package_files
from dotagent.core.errors import UsageError
from dotagent.core.paths import AGENTS_FILE, resource_agent_root
from dotagent.core.utils import slugify, split_front_matter


@dataclass(frozen=True)
class ImportReport:
    source: str
    out: str
    name: str
    files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "out": self.out,
            "name": self.name,
            "files": list(self.files),
            "warnings": list(self.warnings),
        }


def import_claude(
    source: str | Path,
    *,
    out: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
    resource_root: str | Path = "resource",
) -> ImportReport:
    root = Path(source)
    if not root.exists():
        raise UsageError(f"source does not exist: {root}")
    if not root.is_dir():
        raise UsageError(f"expected a directory: {root}")
    claude_dir = root / ".claude"
    if not (claude_dir.exists() or (root / "CLAUDE.md").is_file()):
        raise UsageError(
            f"no Claude Code layout found at {root} "
            "(expected CLAUDE.md or .claude/)"
        )
    agent_name = slugify(name or root.name)
    project_root = (
        Path(out)
        if out
        else resource_agent_root(agent_name, resource_root=resource_root)
    )
    if project_root.exists() and any(project_root.iterdir()) and not force:
        raise UsageError(
            f"destination is not empty: {project_root}; pass --force to overwrite"
        )
    spec_dir = project_root / ".agents"
    spec_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    warnings: list[str] = []
    tools: set[str] = set()
    models: dict[str, str] = {}

    instructions = _read_instructions(root, claude_dir)
    (project_root / AGENTS_FILE).write_text(instructions, encoding="utf-8")
    written.append(project_root / AGENTS_FILE)

    agent_yaml = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Agent",
        "metadata": {
            "name": agent_name,
            "title": agent_name,
            "description": _first_line(instructions) or "Imported from Claude Code.",
            "version": "0.1.0",
        },
        "spec": {},
    }
    if models:
        agent_yaml["spec"]["model"] = next(iter(sorted(models)))
    _write_yaml(spec_dir / "agent.yaml", agent_yaml, written)

    _import_subagents(claude_dir, spec_dir, tools, models, warnings, written)
    _import_skills(claude_dir, spec_dir, tools, warnings, written)
    _import_commands(claude_dir, spec_dir, tools, warnings, written)
    _import_mcp(root, spec_dir, written)

    for tool_name in sorted(tools):
        _write_tool(spec_dir, tool_name, written)
    for model_name, model_value in sorted(models.items()):
        _write_model(spec_dir, model_name, model_value, written)

    return ImportReport(
        source=str(root),
        out=str(project_root),
        name=agent_name,
        files=tuple(str(path) for path in written),
        warnings=tuple(warnings),
    )


def _read_instructions(root: Path, claude_dir: Path) -> str:
    for candidate in (root / "CLAUDE.md", claude_dir / "CLAUDE.md", root / AGENTS_FILE):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return f"# {root.name}\n\nImported from Claude Code.\n"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    return ""


def _write_yaml(path: Path, data: dict, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )
    written.append(path)


def _write_tool(spec_dir: Path, name: str, written: list[Path]) -> None:
    _write_yaml(
        spec_dir / "tools" / f"{name}.yaml",
        {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Tool",
            "metadata": {
                "name": name,
                "description": f"Imported Claude Code tool {name!r}; provide a real input schema.",
            },
            "spec": {
                "input_schema": {"type": "object", "additionalProperties": True},
                "source": {"kind": "external", "ref": f"claude:{name}"},
            },
        },
        written,
    )


def _write_model(spec_dir: Path, name: str, value: str, written: list[Path]) -> None:
    _write_yaml(
        spec_dir / "models" / f"{name}.yaml",
        {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Model",
            "metadata": {"name": name, "description": "Imported from Claude Code."},
            "spec": {"model": value, "provider": "claude-code"},
        },
        written,
    )


def _tool_names(value: object, warnings: list[str], owner: str) -> list[str]:
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw = [str(item).strip() for item in value]
    else:
        return []
    names: list[str] = []
    for item in raw:
        if not item:
            continue
        base = item.split("(")[0].strip()
        if base.startswith("mcp__"):
            warnings.append(
                f"{owner}: MCP tool specifier {item!r} was dropped; declare it as a tool"
            )
            continue
        name = slugify(base)
        if name:
            names.append(name)
    return names


def _model_ref(value: object, models: dict[str, str]) -> str | None:
    if not isinstance(value, str) or not value or value == "inherit":
        return None
    name = slugify(value)
    models.setdefault(name, value)
    return name


def _import_subagents(
    claude_dir: Path,
    spec_dir: Path,
    tools: set[str],
    models: dict[str, str],
    warnings: list[str],
    written: list[Path],
) -> None:
    directory = claude_dir / "agents"
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            document = split_front_matter(text)
        except ValueError as exc:
            warnings.append(f"{path}: skipped ({exc})")
            continue
        front = document.front_matter or {}
        name = slugify(str(front.get("name") or path.stem))
        description = str(front.get("description") or f"Imported subagent {name}.")
        tool_names = _tool_names(front.get("tools"), warnings, str(path))
        tools.update(tool_names)
        model_ref = _model_ref(front.get("model"), models)
        skills = [slugify(str(item)) for item in front.get("skills") or []]
        front_lines_data: dict = {"name": name, "description": description}
        if model_ref:
            front_lines_data["model"] = model_ref
        if tool_names:
            front_lines_data["tools"] = sorted(set(tool_names))
        if skills:
            front_lines_data["skills"] = skills
        header = yaml.safe_dump(
            front_lines_data, sort_keys=False, allow_unicode=True, width=1000
        ).strip()
        destination = spec_dir / "agents" / f"{name}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            f"---\n{header}\n---\n\n" + document.body.strip() + "\n",
            encoding="utf-8",
        )
        written.append(destination)


def _import_skills(
    claude_dir: Path,
    spec_dir: Path,
    tools: set[str],
    warnings: list[str],
    written: list[Path],
) -> None:
    directory = claude_dir / "skills"
    if not directory.is_dir():
        return
    for source_dir in sorted(directory.iterdir()):
        if not source_dir.is_dir():
            continue
        skill_file = source_dir / "SKILL.md"
        if not skill_file.is_file():
            warnings.append(f"{source_dir}: skipped (no SKILL.md)")
            continue
        name = slugify(source_dir.name)
        target = spec_dir / "skills" / name
        shutil.copytree(source_dir, target, dirs_exist_ok=True)
        text = (target / "SKILL.md").read_text(encoding="utf-8")
        try:
            document = split_front_matter(text)
        except ValueError as exc:
            warnings.append(f"{source_dir}: SKILL.md front matter skipped ({exc})")
            document = None
        front = document.front_matter if document and document.front_matter else {}
        allowed = _tool_names(front.get("allowed-tools"), warnings, str(source_dir))
        tools.update(allowed)
        description = str(front.get("description") or f"Imported skill {name}.")
        front_skill: dict = {"name": name, "description": description}
        if front.get("license"):
            front_skill["license"] = front["license"]
        if front.get("compatibility"):
            front_skill["compatibility"] = front["compatibility"]
        if allowed:
            front_skill["allowed-tools"] = " ".join(sorted(set(allowed)))
        if front.get("metadata"):
            front_skill["metadata"] = front["metadata"]
        header = yaml.safe_dump(
            front_skill, sort_keys=False, allow_unicode=True, width=1000
        ).strip()
        body = document.body if document else text
        (target / "SKILL.md").write_text(
            f"---\n{header}\n---\n\n" + body.strip() + "\n", encoding="utf-8"
        )
        written.append(target / "SKILL.md")


def _import_commands(
    claude_dir: Path,
    spec_dir: Path,
    tools: set[str],
    warnings: list[str],
    written: list[Path],
) -> None:
    directory = claude_dir / "commands"
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*.md")):
        relative = path.relative_to(directory).with_suffix("")
        name = slugify("-".join(relative.parts))
        text = path.read_text(encoding="utf-8")
        try:
            document = split_front_matter(text)
        except ValueError as exc:
            warnings.append(f"{path}: skipped ({exc})")
            continue
        front = document.front_matter or {}
        description = str(front.get("description") or f"Imported command {name}.")
        tool_names = _tool_names(front.get("allowed-tools"), warnings, str(path))
        tools.update(tool_names)
        data = {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Task",
            "metadata": {"name": name, "description": description},
            "spec": {"prompt": {"text": document.body.strip() or description}},
        }
        if tool_names:
            data["spec"]["tools"] = sorted(set(tool_names))
        _write_yaml(spec_dir / "tasks" / f"{name}.yaml", data, written)


def _import_mcp(root: Path, spec_dir: Path, written: list[Path]) -> None:
    for candidate in (root / ".mcp.json", root / ".claude" / "mcp.json"):
        if candidate.is_file():
            shutil.copyfile(candidate, spec_dir / "mcp.json")
            written.append(spec_dir / "mcp.json")
            return


def import_package(
    package_path: str | Path,
    *,
    out: str | Path | None = None,
    force: bool = False,
    resource_root: str | Path = "resource",
) -> ImportReport:
    """Recreate an editable project from a DotAgent package."""

    files = read_package_files(Path(package_path))
    try:
        document = yaml.safe_load(files["agent.json"].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise UsageError(f"not a DotAgent package: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("agent"), dict):
        raise UsageError("package agent.json has no agent object")
    agent = document["agent"]
    name = slugify(str(agent.get("name") or "imported-agent"))
    project_root = (
        Path(out)
        if out
        else resource_agent_root(name, resource_root=resource_root)
    )
    if project_root.exists() and any(project_root.iterdir()) and not force:
        raise UsageError(
            f"destination is not empty: {project_root}; pass --force to overwrite"
        )
    spec_dir = project_root / ".agents"
    spec_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    instructions = agent.get("instructions") or {}
    text = instructions.get("text") if isinstance(instructions, dict) else None
    (project_root / AGENTS_FILE).write_text(
        (text or f"# {name}\n") + "\n", encoding="utf-8"
    )
    written.append(project_root / AGENTS_FILE)

    manifest = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Agent",
        "metadata": {
            key: agent[key]
            for key in ("name", "title", "description", "version", "labels", "tags")
            if key in agent
        },
        "spec": {},
    }
    for key in ("model", "defaults", "limits", "settings"):
        if agent.get(key):
            manifest["spec"][key] = agent[key]
    _write_yaml(spec_dir / "agent.yaml", manifest, written)

    for item in (agent.get("knowledge") or {}).values():
        _write_knowledge(spec_dir, item, written)
    for item in (agent.get("skills") or {}).values():
        _write_skill(spec_dir, item, files, written)
    for item in (agent.get("subagents") or {}).values():
        _write_subagent(spec_dir, item, written)
    for item in (agent.get("tools") or {}).values():
        _write_tool_from_ir(spec_dir, item, written)
    for item in (agent.get("models") or {}).values():
        _write_model_from_ir(spec_dir, item, written)
    for item in (agent.get("workflows") or {}).values():
        _write_workflow_from_ir(spec_dir, item, written)
    for item in (agent.get("tasks") or {}).values():
        _write_task_from_ir(spec_dir, item, files, written)
    mcp = agent.get("mcp")
    if isinstance(mcp, dict) and mcp.get("mcpServers"):
        path = spec_dir / "mcp.json"
        path.write_text(json.dumps(mcp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return ImportReport(
        source=str(package_path),
        out=str(project_root),
        name=name,
        files=tuple(str(path) for path in written),
    )


def _write_knowledge(spec_dir: Path, item: dict, written: list[Path]) -> None:
    front = {key: item[key] for key in ("name", "title", "description", "tags", "priority") if item.get(key)}
    path = spec_dir / "knowledge" / f"{slugify(str(item.get('name') or 'knowledge'))}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{header}\n---\n\n{item.get('content', '')}\n", encoding="utf-8")
    written.append(path)


def _write_skill(spec_dir: Path, item: dict, files: dict[str, bytes], written: list[Path]) -> None:
    name = slugify(str(item.get("name") or "skill"))
    front = {"name": name, "description": item.get("description") or ""}
    for key in ("license", "compatibility", "metadata"):
        if item.get(key):
            front[key] = item[key]
    if item.get("allowed_tools"):
        front["allowed-tools"] = " ".join(item["allowed_tools"])
    directory = spec_dir / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    (directory / "SKILL.md").write_text(
        f"---\n{header}\n---\n\n{item.get('instructions', '')}\n", encoding="utf-8"
    )
    written.append(directory / "SKILL.md")
    for asset in item.get("files") or []:
        source = files.get(f"assets/{asset}")
        if source is None:
            continue
        relative = Path(str(asset)).relative_to(Path(f"skills/{name}"))
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        written.append(target)


def _write_subagent(spec_dir: Path, item: dict, written: list[Path]) -> None:
    front = {
        key: item[key]
        for key in ("name", "description", "title", "version", "tags", "model", "tools", "skills")
        if item.get(key)
    }
    path = spec_dir / "agents" / f"{slugify(str(item.get('name') or 'agent'))}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{header}\n---\n\n{item.get('instructions', '')}\n", encoding="utf-8")
    written.append(path)


def _write_tool_from_ir(spec_dir: Path, item: dict, written: list[Path]) -> None:
    data = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Tool",
        "metadata": {
            key: item[key]
            for key in ("name", "title", "description", "version", "labels", "tags")
            if item.get(key)
        },
        "spec": {
            key: item[key]
            for key in ("input_schema", "output_schema", "annotations", "source")
            if item.get(key)
        },
    }
    _write_yaml(spec_dir / "tools" / f"{slugify(str(item.get('name') or 'tool'))}.yaml", data, written)


def _write_model_from_ir(spec_dir: Path, item: dict, written: list[Path]) -> None:
    data = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Model",
        "metadata": {
            key: item[key]
            for key in ("name", "title", "description", "version", "labels", "tags")
            if item.get(key)
        },
        "spec": {
            key: item[key]
            for key in (
                "model",
                "provider",
                "context_window",
                "max_output_tokens",
                "capabilities",
                "endpoint",
                "parameters",
                "provider_options",
            )
            if item.get(key)
        },
    }
    _write_yaml(spec_dir / "models" / f"{slugify(str(item.get('name') or 'model'))}.yaml", data, written)


def _write_workflow_from_ir(spec_dir: Path, item: dict, written: list[Path]) -> None:
    data = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Workflow",
        "metadata": {key: item[key] for key in ("name", "title", "description", "version", "labels", "tags") if item.get(key)},
        "spec": {"entry": item.get("entry"), "nodes": item.get("nodes") or [], "edges": item.get("edges") or []},
    }
    _write_yaml(spec_dir / "workflows" / f"{slugify(str(item.get('name') or 'workflow'))}.yaml", data, written)


def _write_task_from_ir(spec_dir: Path, item: dict, files: dict[str, bytes], written: list[Path]) -> None:
    spec: dict = {}
    prompt = item.get("prompt")
    if isinstance(prompt, dict):
        spec["prompt"] = {"text": prompt.get("text", "")}
    for key in ("input_schema", "output_schema", "skills", "tools", "workflow", "examples"):
        if item.get(key):
            spec[key] = item[key]
    data = {
        "apiVersion": "dotagent/v1alpha1",
        "kind": "Task",
        "metadata": {
            key: item[key]
            for key in ("name", "title", "description", "version", "labels", "tags")
            if item.get(key)
        },
        "spec": spec,
    }
    _write_yaml(spec_dir / "tasks" / f"{slugify(str(item.get('name') or 'task'))}.yaml", data, written)


__all__ = ["ImportReport", "import_claude", "import_package"]
