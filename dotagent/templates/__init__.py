"""Agent skeleton templates and the ``dotagent init`` implementation."""

from __future__ import annotations

from pathlib import Path

from dotagent.core.errors import UsageError
from dotagent.core.paths import resource_agent_root
from dotagent.core.utils import slugify

_MINIMAL = {
    "AGENTS.md": """\
# Role

You are a helpful agent. Describe the job, the constraints and the tone here.
""",
    ".agents/agent.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Agent
metadata:
  name: __NAME__
  title: My Agent
  description: Describe what this agent is for.
  version: 0.1.0
spec:
  model: default
""",
    ".agents/models/default.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Model
metadata:
  name: default
  description: Placeholder model; set the id and endpoint for your provider.
spec:
  model: your-model-id
  provider: openai-compatible
""",
}

_FULL = {
    "AGENTS.md": """\
# Role

You are the example agent. Replace these instructions with the real job.

## Working style

- State assumptions before acting.
- Prefer primary sources.
""",
    ".agents/agent.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Agent
metadata:
  name: __NAME__
  title: Example Agent
  description: A complete DotAgent skeleton with one of every entity type.
  version: 0.1.0
  tags:
    - example
spec:
  model: default
  defaults:
    temperature: 0.2
  limits:
    knowledge_chars: 20000
    tool_count: 16
""",
    ".agents/knowledge/overview.md": """\
---
name: overview
title: Overview
description: What this example agent knows.
tags:
  - example
---

This knowledge file is plain Markdown. Files under knowledge/ are injected
into the agent context by the host that runs it.
""",
    ".agents/skills/example/SKILL.md": """\
---
name: example
description: Show how an Agent Skills compliant skill is written.
---

# Example skill

Explain the procedure this skill performs. Keep it short and actionable.
""",
    ".agents/agents/reviewer.md": """\
---
name: reviewer
description: Reviews a draft and lists concrete improvements.
tools:
  - echo
uses:
  - helper
---

You are a reviewer. Return a short list of concrete improvements.
""",
    ".agents/agents/helper.md": """\
---
name: helper
description: A reusable child agent that echoes a draft.
tools:
  - echo
model: default
---

Echo the draft unchanged and point out missing sections.
""",
    ".agents/tools/echo.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Tool
metadata:
  name: echo
  description: Return the provided text unchanged.
spec:
  input_schema:
    type: object
    additionalProperties: false
    required:
      - text
    properties:
      text:
        type: string
        minLength: 1
  annotations:
    read_only: true
    idempotent: true
""",
    ".agents/tools/echo-upper.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Tool
extends: echo
metadata:
  name: echo-upper
  description: Return the provided text in upper case.
""",
    ".agents/audit.yaml": """\
apiVersion: dotagent/v1alpha1
kind: AuditConfig
rules:
  AUD006:
    severity: info
budget_warning_ratio: 0.8
""",
    ".agents/models/default.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Model
metadata:
  name: default
  description: Placeholder model; set the id and endpoint for your provider.
spec:
  model: your-model-id
  provider: openai-compatible
""",
    ".agents/workflows/main.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Workflow
metadata:
  name: main
  description: Run the example task.
spec:
  entry: run
  nodes:
    - id: run
      kind: task
      ref: run
      outputs:
        - result
  edges: []
""",
    ".agents/tasks/run.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Task
metadata:
  name: run
  description: Run the example task.
spec:
  prompt:
    text: Say hello and name the tools you could use.
  tools:
    - echo
""",
    ".agents/mcp.json": """\
{
  "mcpServers": {
    "example": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"]
    }
  }
}
""",
    ".agents/tests/checks.yaml": """\
apiVersion: dotagent/v1alpha1
kind: Checks
checks:
  - id: has-echo-tool
    description: The echo tool is declared.
    path: tools.echo
    op: exists
""",
}

TEMPLATES: dict[str, dict[str, str]] = {"minimal": _MINIMAL, "full": _FULL}


def init_agent(
    name: str = "my-agent",
    *,
    path: str | Path | None = None,
    template: str = "minimal",
    force: bool = False,
    resource_root: str | Path = "resource",
) -> Path:
    """Create a new agent project and return its project root.

    By default the agent is created under ``<resource_root>/agent/<name>/``.
    """

    if template not in TEMPLATES:
        raise UsageError(
            f"unknown template {template!r}; available: {', '.join(sorted(TEMPLATES))}"
        )
    agent_name = slugify(name)
    if not agent_name:
        raise UsageError(f"invalid agent name: {name!r}")
    if path is not None:
        base = Path(path)
        project_root = base.parent if base.name == ".agents" else base
    else:
        project_root = resource_agent_root(agent_name, resource_root=resource_root)
    if project_root.exists() and any(project_root.iterdir()) and not force:
        raise UsageError(
            f"agent already exists at {project_root}; pass --force to overwrite template files"
        )
    files = TEMPLATES[template]
    for relative, content in files.items():
        destination = project_root.joinpath(*Path(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content.replace("__NAME__", agent_name), encoding="utf-8")
    return project_root


__all__ = ["TEMPLATES", "init_agent"]
