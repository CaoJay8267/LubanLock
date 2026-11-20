"""Import GitHub Copilot custom instructions into DotAgent."""

from __future__ import annotations

from pathlib import Path

from dotagent.core.errors import UsageError
from dotagent.core.paths import AGENTS_FILE, resource_agent_root
from dotagent.core.utils import slugify
from dotagent.integrations.claude import ImportReport, _write_yaml


def looks_like_copilot(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.is_file():
        return candidate.name == "copilot-instructions.md"
    return (candidate / ".github" / "copilot-instructions.md").is_file()


def import_copilot(
    source: str | Path,
    *,
    out: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
    resource_root: str | Path = "resource",
) -> ImportReport:
    root = Path(source)
    if root.is_file():
        instructions_path = root
        inferred = root.parent.parent.name or root.stem
    else:
        instructions_path = root / ".github" / "copilot-instructions.md"
        inferred = root.name
    if not instructions_path.is_file():
        raise UsageError(f"cannot find .github/copilot-instructions.md in {root}")
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {instructions_path}: {exc}") from exc
    agent_name = slugify(name or inferred or "copilot-agent")
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
    (project_root / AGENTS_FILE).write_text(instructions.rstrip() + "\n", encoding="utf-8")
    files = [project_root / AGENTS_FILE]
    instructions_dir = (
        instructions_path.parent / "instructions"
        if instructions_path.parent.name == ".github"
        else root / ".github" / "instructions"
    )
    if instructions_dir.is_dir():
        for path in sorted(instructions_dir.glob("*.instructions.md")):
            text = path.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                text = parts[2] if len(parts) == 3 else text
            knowledge = project_root / ".agents" / "knowledge" / (
                slugify(path.name.removesuffix(".instructions.md")) + ".md"
            )
            knowledge.parent.mkdir(parents=True, exist_ok=True)
            knowledge.write_text(
                f"---\nname: {knowledge.stem}\ndescription: Imported Copilot instruction.\n"
                f"---\n\n{text.strip()}\n",
                encoding="utf-8",
            )
            files.append(knowledge)
    _write_yaml(
        spec_dir / "agent.yaml",
        {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Agent",
            "metadata": {
                "name": agent_name,
                "title": agent_name,
                "description": "Imported from GitHub Copilot instructions.",
                "version": "0.1.0",
            },
            "spec": {},
        },
        files,
    )
    return ImportReport(
        source=str(root),
        out=str(project_root),
        name=agent_name,
        files=tuple(str(path) for path in files),
    )


__all__ = ["import_copilot", "looks_like_copilot"]
