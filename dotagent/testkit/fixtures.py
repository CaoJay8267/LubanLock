"""Reusable fixtures for testing DotAgent projects and packages.

These helpers are part of the package (not the test directory) so that
downstream projects can build temporary agents with the same conventions.
"""

from __future__ import annotations

import shutil
from pathlib import Path

AGENTS_MD = "AGENTS.md"


def write_project(root: str | Path, files: dict[str, str]) -> Path:
    """Write project-relative files and return the project root."""

    project = Path(root)
    for relative, content in files.items():
        destination = project.joinpath(*Path(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    return project


def copy_project(source: str | Path, destination: str | Path) -> Path:
    target = Path(destination) / Path(source).name
    shutil.copytree(source, target)
    return target


def minimal_project(name: str = "test-agent") -> dict[str, str]:
    """A valid minimal project with AGENTS.md, manifest and model."""

    return {
        AGENTS_MD: "# Role\n\nDo the thing carefully.\n",
        ".agents/agent.yaml": (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Agent\n"
            "metadata:\n"
            f"  name: {name}\n"
            "  description: A test agent.\n"
            "spec:\n"
            "  model: default\n"
        ),
        ".agents/models/default.yaml": (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Model\n"
            "metadata:\n"
            "  name: default\n"
            "  description: Test model.\n"
            "spec:\n"
            "  model: test-model\n"
        ),
    }


def tool_yaml(
    name: str = "echo",
    *,
    schema: str | None = None,
    description: str = "Return the text.",
) -> str:
    schema = schema or (
        "    type: object\n"
        "    additionalProperties: false\n"
        "    required: [text]\n"
        "    properties:\n"
        "      text:\n"
        "        type: string\n"
    )
    return (
        "apiVersion: dotagent/v1alpha1\n"
        "kind: Tool\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  description: {description}\n"
        "spec:\n"
        "  input_schema:\n" + schema
    )


def skill_front_matter(name: str, description: str = "A test skill.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        "# Skill\n\nDo the skill.\n"
    )


__all__ = [
    "AGENTS_MD",
    "copy_project",
    "minimal_project",
    "skill_front_matter",
    "tool_yaml",
    "write_project",
]
