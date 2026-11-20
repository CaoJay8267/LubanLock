"""Built-in projectors: neutral JSON, human Markdown, and Claude Code."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import ClassVar

import yaml

from dotagent.build.compiler import CompiledAgent
from dotagent.core.errors import Diagnostic, Severity
from dotagent.core.models import Skill, Task, Workflow
from dotagent.core.utils import slugify

from .base import ProjectedFile, ProjectionResult, Projector


def _title(metadata) -> str:
    return metadata.title or metadata.name


_CLAUDE_MODEL = re.compile(
    r"^(?:inherit|sonnet|opus|haiku|fable|claude-[A-Za-z0-9._-]+)$"
)


def _is_claude_model(value: str) -> bool:
    return bool(_CLAUDE_MODEL.match(value or ""))


def _cell(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _mermaid(workflow: Workflow) -> str:
    lines = ["flowchart TD"]
    for node in workflow.nodes:
        label = node.title or node.id
        suffix = node.kind + (f": {node.ref}" if node.ref else "")
        label = label.replace('"', "#quot;")
        lines.append(f'  {node.id}["{label} ({suffix})"]')
    for edge in workflow.edges:
        label = f"|{edge.label}|" if edge.label else ""
        lines.append(f"  {edge.source} -->{label} {edge.target}")
    return "\n".join(lines)


class JsonProjector(Projector):
    name = "json"
    description = "Neutral machine-readable bundle: agent.json plus every asset."

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        files = [
            ProjectedFile.text(
                "agent.json", compiled.to_json_text(), media_type="application/json"
            )
        ]
        for asset in compiled.assets:
            files.append(ProjectedFile(f"assets/{asset.path}", asset.data, asset.media_type))
        return ProjectionResult(target=self.name, files=tuple(files))


class MarkdownProjector(Projector):
    name = "markdown"
    description = "Readable documentation bundle for humans and code review."

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        agent = compiled.definition
        files: list[ProjectedFile] = [
            ProjectedFile.text("README.md", self._readme(compiled)),
        ]
        if agent.instructions is not None:
            files.append(
                ProjectedFile.text("instructions.md", agent.instructions.text + "\n")
            )
        for knowledge in agent.knowledge:
            files.append(
                ProjectedFile.text(f"knowledge/{knowledge.metadata.name}.md", knowledge.content + "\n")
            )
        for skill in agent.skills:
            files.append(ProjectedFile.text(f"skills/{skill.name}.md", self._skill(skill)))
        for tool in agent.tools:
            files.append(
                ProjectedFile(
                    path=f"tools/{tool.metadata.name}.json",
                    data=(json.dumps(tool.to_dict(), indent=2, sort_keys=True) + "\n").encode(),
                    media_type="application/json",
                )
            )
        for workflow in agent.workflows:
            files.append(
                ProjectedFile.text(
                    f"workflows/{workflow.metadata.name}.md", self._workflow(workflow)
                )
            )
        for task in agent.tasks:
            files.append(
                ProjectedFile.text(f"tasks/{task.metadata.name}.md", self._task(task))
            )
        return ProjectionResult(target=self.name, files=tuple(files))

    def _readme(self, compiled: CompiledAgent) -> str:
        agent = compiled.definition
        lines = [f"# {_title(agent.metadata)}", ""]
        if agent.metadata.description:
            lines.extend([agent.metadata.description, ""])
        lines.append(f"- Version: `{agent.metadata.version}`")
        lines.append(f"- Fingerprint: `{compiled.fingerprint}`")
        counts = agent.entity_counts()
        lines.append(
            "- Entities: "
            + ", ".join(f"{count} {name}" for name, count in counts.items() if count)
        )
        lines.append("")
        lines.append("## Instructions")
        lines.append("")
        if agent.instructions is not None:
            lines.append("See [instructions.md](instructions.md).")
        else:
            lines.append("_No instructions._")
        if agent.knowledge:
            lines.extend(["", "## Knowledge", "", "| Name | Title | Priority |", "| --- | --- | --- |"])
            for item in agent.knowledge:
                lines.append(
                    f"| [{item.metadata.name}](knowledge/{item.metadata.name}.md) "
                    f"| {_cell(item.metadata.title or '')} | {item.priority} |"
                )
        if agent.skills:
            lines.extend(["", "## Skills", "", "| Name | Description |", "| --- | --- |"])
            for skill in agent.skills:
                lines.append(
                    f"| [{skill.name}](skills/{skill.name}.md) "
                    f"| {_cell(skill.description or '')} |"
                )
        if agent.tools:
            lines.extend(
                ["", "## Tools", "", "| Name | Description | Read-only |", "| --- | --- | --- |"]
            )
            for tool in agent.tools:
                annotations = dict(tool.annotations)
                read_only = "yes" if annotations.get("read_only") else "no"
                lines.append(
                    f"| `{tool.metadata.name}` | {_cell(tool.metadata.description or '')} | {read_only} |"
                )
        if agent.subagents:
            lines.extend(["", "## Subagents", "", "| Name | Description |", "| --- | --- |"])
            for subagent in agent.subagents:
                lines.append(
                    f"| `{subagent.metadata.name}` | {_cell(subagent.metadata.description or '')} |"
                )
        if agent.workflows:
            lines.extend(["", "## Workflows", ""])
            for workflow in agent.workflows:
                lines.append(f"### {workflow.metadata.name}")
                lines.append("")
                if workflow.metadata.description:
                    lines.extend([workflow.metadata.description, ""])
                lines.extend(["```mermaid", _mermaid(workflow), "```", ""])
                lines.append(f"Entry node: `{workflow.entry}`. Details: [workflow page](workflows/{workflow.metadata.name}.md).")
                lines.append("")
        if agent.tasks:
            lines.extend(["", "## Tasks", "", "| Name | Description |", "| --- | --- |"])
            for task in agent.tasks:
                lines.append(
                    f"| [{task.metadata.name}](tasks/{task.metadata.name}.md) "
                    f"| {_cell(task.metadata.description or '')} |"
                )
        if agent.models:
            lines.extend(["", "## Models", "", "| Name | Model | Provider |", "| --- | --- | --- |"])
            for model in agent.models:
                lines.append(
                    f"| `{model.metadata.name}` | `{model.model}` | {model.provider or ''} |"
                )
        if agent.defaults:
            lines.extend(["", "## Defaults", "", "```json", json.dumps(dict(agent.defaults), indent=2, sort_keys=True), "```"])
        lines.append("")
        return "\n".join(lines)

    def _skill(self, skill: Skill) -> str:
        lines = [f"# {skill.name}", ""]
        if skill.description:
            lines.extend([skill.description, ""])
        if skill.allowed_tools:
            lines.extend(["Allowed tools: " + ", ".join(f"`{name}`" for name in skill.allowed_tools), ""])
        lines.extend([skill.instructions, ""])
        return "\n".join(lines)

    def _workflow(self, workflow: Workflow) -> str:
        lines = [f"# {_title(workflow.metadata)}", ""]
        if workflow.metadata.description:
            lines.extend([workflow.metadata.description, ""])
        lines.extend(["```mermaid", _mermaid(workflow), "```", ""])
        lines.extend(
            [
                "| Node | Kind | Ref | Inputs | Outputs |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for node in workflow.nodes:
            lines.append(
                f"| `{node.id}` | {node.kind} | {node.ref or ''} "
                f"| {', '.join(node.inputs)} | {', '.join(node.outputs)} |"
            )
        lines.append("")
        return "\n".join(lines)

    def _task(self, task: Task) -> str:
        lines = [f"# {_title(task.metadata)}", ""]
        if task.metadata.description:
            lines.extend([task.metadata.description, ""])
        if task.prompt:
            lines.extend(["## Prompt", "", task.prompt.text, ""])
        if task.tools:
            lines.extend(["Tools: " + ", ".join(f"`{name}`" for name in task.tools), ""])
        if task.skills:
            lines.extend(["Skills: " + ", ".join(f"`{name}`" for name in task.skills), ""])
        if task.workflow:
            lines.extend([f"Workflow: `{task.workflow}`", ""])
        if task.input_schema:
            lines.extend(["## Input schema", "", "```json", json.dumps(task.input_schema, indent=2, sort_keys=True), "```", ""])
        if task.output_schema:
            lines.extend(["## Output schema", "", "```json", json.dumps(task.output_schema, indent=2, sort_keys=True), "```", ""])
        return "\n".join(lines)


#: Claude Code built-in tool names, mapped from common DotAgent tool names.
_CLAUDE_TOOL_ALIASES = {
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "bash": "Bash",
    "shell": "Bash",
    "web_fetch": "WebFetch",
    "webfetch": "WebFetch",
    "web_search": "WebSearch",
    "websearch": "WebSearch",
    "task": "Task",
    "todo_write": "TodoWrite",
    "notebook_edit": "NotebookEdit",
}


class ClaudeProjector(Projector):
    name = "claude"
    description = (
        "Claude Code layout: CLAUDE.md, .claude/agents (subagents), "
        ".claude/skills (skills) and .claude/commands (tasks)."
    )

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        agent = compiled.definition
        warnings: list[Diagnostic] = []
        files: list[ProjectedFile] = [ProjectedFile.text("CLAUDE.md", self._claude_md(compiled))]
        if agent.mcp:
            files.append(
                ProjectedFile.text(
                    ".mcp.json",
                    json.dumps({"mcpServers": dict(agent.mcp)}, indent=2, sort_keys=True)
                    + "\n",
                    media_type="application/json",
                )
            )
        for subagent in agent.subagents:
            files.append(
                ProjectedFile.text(
                    f".claude/agents/{subagent.metadata.name}.md",
                    self._subagent(compiled, subagent, warnings),
                )
            )
        for skill in agent.skills:
            files.append(
                ProjectedFile.text(
                    f".claude/skills/{skill.name}/SKILL.md",
                    self._skill(skill, warnings),
                )
            )
            skill_root = PurePosixPath(skill.file).parent
            for asset_path in skill.files:
                relative = PurePosixPath(asset_path).relative_to(skill_root)
                asset = compiled.asset(asset_path)
                if asset is None:  # pragma: no cover - compiler guarantees this
                    continue
                files.append(
                    ProjectedFile(
                        f".claude/skills/{skill.name}/{relative.as_posix()}",
                        asset.data,
                        asset.media_type,
                    )
                )
        for task in agent.tasks:
            files.append(
                ProjectedFile.text(
                    f".claude/commands/{task.metadata.name}.md",
                    self._command(task, warnings),
                )
            )
        return ProjectionResult(
            target=self.name, files=tuple(files), warnings=tuple(warnings)
        )

    def _claude_md(self, compiled: CompiledAgent) -> str:
        agent = compiled.definition
        lines = [f"# {_title(agent.metadata)}", ""]
        if agent.metadata.description:
            lines.extend([agent.metadata.description, ""])
        if agent.instructions is not None:
            lines.extend(["## Instructions", "", agent.instructions.text, ""])
        if agent.skills:
            lines.extend(["## Skills", ""])
            for skill in agent.skills:
                lines.append(f"- `{skill.name}` — {skill.description or ''}")
            lines.append("")
        if agent.subagents:
            lines.extend(["## Subagents", ""])
            for subagent in agent.subagents:
                lines.append(
                    f"- `{subagent.metadata.name}` — {subagent.metadata.description or ''}"
                )
            lines.append("")
        if agent.tools:
            lines.extend(
                [
                    "## Tools",
                    "",
                    "DotAgent declares these tools; execution is provided by Claude Code",
                    "built-ins, MCP servers or settings:",
                    "",
                ]
            )
            for tool in agent.tools:
                mapped = self._map_tool(tool.metadata.name)
                hint = mapped or "needs MCP/settings"
                lines.append(f"- `{tool.metadata.name}` ({hint}) — {tool.metadata.description or ''}")
            lines.append("")
        if agent.tasks:
            lines.extend(["## Commands", ""])
            for task in agent.tasks:
                lines.append(f"- `/{task.metadata.name}` — {task.metadata.description or ''}")
            lines.append("")
        if agent.mcp:
            lines.extend(["## MCP servers", ""])
            for name in dict(agent.mcp):
                lines.append(f"- `{name}`")
            lines.append("")
        lines.extend(
            [
                "---",
                "Generated by DotAgent. Review the mapping notes in the DotAgent",
                "architecture document before committing generated files.",
                "",
            ]
        )
        return "\n".join(lines)

    def _subagent(
        self, compiled: CompiledAgent, subagent, warnings: list[Diagnostic]
    ) -> str:
        agent = compiled.definition
        front: dict = {
            "name": subagent.metadata.name,
            "description": subagent.metadata.description or subagent.metadata.title or "",
        }
        mapped_tools = self._map_tools(subagent.tools, warnings, subagent.file)
        if mapped_tools:
            front["tools"] = ", ".join(mapped_tools)
        if subagent.model:
            model = agent.model_by_name(subagent.model)
            value = model.model if model else subagent.model
            if _is_claude_model(value):
                front["model"] = value
            else:
                warnings.append(
                    Diagnostic(
                        "DTA504",
                        f"model {subagent.model!r} ({value!r}) is not a Claude Code "
                        "model id and was omitted",
                        Severity.WARNING,
                        path=subagent.file,
                    )
                )
        if subagent.skills:
            front["skills"] = list(subagent.skills)
        if subagent.agents:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    f"nested child agents {list(subagent.agents)!r} are not represented "
                    "in the Claude Code subagent format",
                    Severity.WARNING,
                    path=subagent.file,
                )
            )
        return self._front_matter(front) + subagent.instructions + "\n"

    def _skill(self, skill: Skill, warnings: list[Diagnostic]) -> str:
        front: dict = {"name": skill.name, "description": skill.description}
        if skill.license:
            front["license"] = skill.license
        if skill.compatibility:
            front["compatibility"] = skill.compatibility
        if skill.metadata:
            front["metadata"] = dict(skill.metadata)
        mapped_tools = self._map_tools(skill.allowed_tools, warnings, skill.file)
        if mapped_tools:
            front["allowed-tools"] = " ".join(mapped_tools)
        return self._front_matter(front) + skill.instructions + "\n"

    def _command(self, task: Task, warnings: list[Diagnostic]) -> str:
        front: dict = {
            "description": task.metadata.description or task.metadata.title or task.metadata.name,
        }
        mapped_tools = self._map_tools(task.tools, warnings, task.file)
        if mapped_tools:
            front["allowed-tools"] = " ".join(mapped_tools)
        body = task.prompt.text if task.prompt else f"Run the {task.metadata.name} task."
        return self._front_matter(front) + body + "\n"

    def _map_tools(
        self, names: tuple[str, ...], warnings: list[Diagnostic], path: str
    ) -> list[str]:
        mapped: list[str] = []
        for name in names:
            value = self._map_tool(name)
            if value:
                mapped.append(value)
            else:
                warnings.append(
                    Diagnostic(
                        "DTA504",
                        f"tool {name!r} has no Claude Code built-in equivalent and "
                        "was omitted; provide it through MCP or settings",
                        Severity.WARNING,
                        path=path,
                    )
                )
        return mapped

    def _map_tool(self, name: str) -> str | None:
        if name in _CLAUDE_TOOL_ALIASES:
            return _CLAUDE_TOOL_ALIASES[name]
        if name.startswith("mcp__"):
            return name
        return None

    @staticmethod
    def _front_matter(front: dict) -> str:
        rendered = yaml.safe_dump(
            front, sort_keys=False, allow_unicode=True, width=1000
        ).strip()
        return f"---\n{rendered}\n---\n\n"


class DshProjector(Projector):
    name = "dsh"
    description = (
        "DeepSeek Harness preset bundle: package.json + cordis.patch.yml, "
        "persona plus known DSH tool plugins."
    )

    _TOOL_PLUGINS: ClassVar[dict[str, str]] = {
        "read": "@deepseek-ai/dsh-tool-fs",
        "write": "@deepseek-ai/dsh-tool-fs",
        "edit": "@deepseek-ai/dsh-tool-fs",
        "glob": "@deepseek-ai/dsh-tool-fs-search",
        "grep": "@deepseek-ai/dsh-tool-fs-search",
        "bash": "@deepseek-ai/dsh-tool-bash",
        "shell": "@deepseek-ai/dsh-tool-bash",
        "exec": "@deepseek-ai/dsh-tool-bash",
        "pwsh": "@deepseek-ai/dsh-tool-pwsh",
        "powershell": "@deepseek-ai/dsh-tool-pwsh",
        "web_search": "@deepseek-ai/dsh-tool-web",
        "web_fetch": "@deepseek-ai/dsh-tool-web",
        "web-search": "@deepseek-ai/dsh-tool-web",
        "web-fetch": "@deepseek-ai/dsh-tool-web",
        "todo_write": "@deepseek-ai/dsh-tool-todo",
        "todo": "@deepseek-ai/dsh-tool-todo",
        "skill": "@deepseek-ai/dsh-tool-skill",
        "workflow": "@deepseek-ai/dsh-tool-workflow",
    }

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        agent = compiled.definition
        preset_id = slugify(agent.metadata.name)
        warnings: list[Diagnostic] = []
        plugins: list[dict] = []
        if agent.instructions and agent.instructions.text.strip():
            plugins.append(
                {
                    "id": "persona",
                    "name": "@deepseek-ai/dsh-persona",
                    "config": {"prefix": agent.instructions.text.strip()},
                }
            )
        else:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "agent has no instructions; the DSH preset will not install a persona",
                    Severity.WARNING,
                )
            )
        mapped: set[str] = set()
        unmapped: list[str] = []
        for tool in agent.tools:
            plugin = self._TOOL_PLUGINS.get(tool.metadata.name)
            if plugin is None:
                plugin = self._TOOL_PLUGINS.get(tool.metadata.name.replace("-", "_"))
            if plugin is None:
                unmapped.append(tool.metadata.name)
                continue
            mapped.add(plugin)
        for plugin in sorted(mapped):
            plugins.append(
                {
                    "id": f"tool-{slugify(plugin.rsplit('-', 1)[-1])}",
                    "name": plugin,
                }
            )
        if unmapped:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "tools without a known DSH plugin were not mapped: "
                    + ", ".join(sorted(unmapped)),
                    Severity.WARNING,
                )
            )
        config = {
            "id": preset_id,
            "name": agent.metadata.title or agent.metadata.name,
            "description": agent.metadata.description or "",
            "order": 10,
            "plugins": plugins,
        }
        patch = [
            {
                "insert": [
                    {
                        "id": f"preset-{preset_id}",
                        "name": "@deepseek-ai/dsh-agent-preset",
                        "config": config,
                    }
                ]
            }
        ]
        package = {
            "name": f"@local/dotagent-{preset_id}",
            "version": agent.metadata.version,
            "private": True,
            "type": "module",
            "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
        }
        files = [
            ProjectedFile.text(
                "dsh-preset/package.json",
                json.dumps(package, indent=2, ensure_ascii=False) + "\n",
                media_type="application/json",
            ),
            ProjectedFile.text(
                "dsh-preset/cordis.patch.yml",
                yaml.safe_dump(patch, sort_keys=False, allow_unicode=True, width=1000),
            ),
            ProjectedFile.text(
                "dsh-preset/README.md",
                self._readme(agent, preset_id),
            ),
        ]
        for label, items in (
            ("skills", agent.skills),
            ("subagents", agent.subagents),
            ("workflows", agent.workflows),
            ("tasks", agent.tasks),
        ):
            if items:
                warnings.append(
                    Diagnostic(
                        "DTA504",
                        f"{label} are not mapped into the DSH preset bundle; "
                        "install the matching DSH plugins manually",
                        Severity.WARNING,
                    )
                )
        if agent.mcp:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "MCP servers are not mapped into the DSH preset bundle",
                    Severity.WARNING,
                )
            )
        return ProjectionResult(
            target=self.name, files=tuple(files), warnings=tuple(warnings)
        )

    @staticmethod
    def _readme(agent, preset_id: str) -> str:
        return f"""# DSH preset: {agent.metadata.name}

This directory is a DeepSeek Harness bundle skeleton generated by DotAgent.

- `package.json` declares the bundle patch.
- `cordis.patch.yml` declares the agent preset with a persona prefix.

Install it with the DSH plugin manager (`install_bundle`) or copy it into your
bundle workspace. Tools, skills and MCP servers are not mapped automatically;
install matching DSH plugins and extend the `plugins` list.
"""


class OpenAIAgentsProjector(Projector):
    name = "openai"
    description = (
        "OpenAI Agents SDK scaffold (agent.py + tools.py) with generated "
        "handlers for simple JSON schemas."
    )

    _SCALARS: ClassVar[dict[str, str]] = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        agent = compiled.definition
        warnings: list[Diagnostic] = []
        model_id = self._model_id(agent)
        handlers, complex_tools = self._handlers(agent)
        lines = [
            '"""Generated by DotAgent. Implement tool handler bodies before running."""',
            "",
        ]
        lines.append("from agents import Agent")
        if handlers:
            lines.append(
                "from tools import " + ", ".join(func for _, func, _, _ in handlers)
            )
        lines.extend(
            [
                "",
                "agent = Agent(",
                f"    name={json.dumps(agent.metadata.name)},",
            ]
        )
        if agent.instructions and agent.instructions.text.strip():
            lines.append(
                f"    instructions={json.dumps(agent.instructions.text.strip())},"
            )
        if model_id:
            lines.append(f"    model={json.dumps(model_id)},")
        else:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "no concrete model id was found; the OpenAI agent uses the SDK default",
                    Severity.WARNING,
                )
            )
        if handlers:
            lines.append(
                "    tools=["
                + ", ".join(func for _, func, _, _ in handlers)
                + "],"
            )
        lines.append(")")
        lines.append("")
        files = [
            ProjectedFile.text("openai/agent.py", "\n".join(lines)),
            ProjectedFile.text(
                "openai/tools.json",
                json.dumps(self._tools(agent), indent=2, ensure_ascii=False) + "\n",
                media_type="application/json",
            ),
        ]
        if handlers:
            files.append(
                ProjectedFile.text(
                    "openai/tools.py",
                    self._handlers_source(handlers, agent),
                )
            )
        files.append(
            ProjectedFile.text(
                "openai/README.md",
                "# OpenAI Agents SDK projection\n\n"
                "`agent.py` and `tools.py` are generated. Handler bodies raise "
                "`NotImplementedError`; implement them or replace them with real "
                "clients. `tools.json` keeps the original JSON Schemas. Subagents, "
                "skills, workflows and MCP declarations are not represented.\n",
            )
        )
        if complex_tools:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "tools with nested/unsupported schemas keep only their JSON Schema: "
                    + ", ".join(sorted(complex_tools)),
                    Severity.WARNING,
                )
            )
        if agent.tools and not handlers:
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "tool schemas are emitted but no handler could be generated",
                    Severity.WARNING,
                )
            )
        return ProjectionResult(
            target=self.name, files=tuple(files), warnings=tuple(warnings)
        )

    @classmethod
    def _handlers(cls, agent):
        handlers = []
        complex_tools: list[str] = []
        used: set[str] = set()
        for tool in agent.tools:
            schema = tool.input_schema
            if not isinstance(schema, dict):
                complex_tools.append(tool.metadata.name)
                continue
            properties = schema.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            required = schema.get("required")
            required = set(required) if isinstance(required, list) else set()
            params = []
            supported = True
            for name, prop in properties.items():
                if (
                    not isinstance(name, str)
                    or not name.isidentifier()
                    or not isinstance(prop, dict)
                ):
                    supported = False
                    break
                type_name = cls._python_type(prop)
                if type_name is None:
                    supported = False
                    break
                suffix = "" if name in required else " | None = None"
                params.append(f"{name}: {type_name}{suffix}")
            if not supported:
                complex_tools.append(tool.metadata.name)
                continue
            params.sort(key=lambda item: " = None" in item)
            func = re.sub(r"[^0-9A-Za-z_]", "_", tool.metadata.name)
            if not func or func[0].isdigit():
                func = f"tool_{func}"
            while func in used:
                func += "_"
            used.add(func)
            description = (tool.metadata.description or "").replace('"""', "'''")
            handlers.append((tool.metadata.name, func, params, description))
        return handlers, complex_tools

    @classmethod
    def _python_type(cls, prop: dict) -> str | None:
        type_name = prop.get("type")
        if type_name in cls._SCALARS:
            return cls._SCALARS[type_name]
        if type_name == "array":
            item = prop.get("items")
            if isinstance(item, dict):
                inner_type = item.get("type")
                if isinstance(inner_type, str):
                    inner = cls._SCALARS.get(inner_type)
                    if inner:
                        return f"list[{inner}]"
        return None

    @staticmethod
    def _handlers_source(handlers, agent) -> str:
        lines = ['"""Generated tool handlers. Implement each body."""', "", "from agents import function_tool", ""]
        for name, func, params, description in handlers:
            lines.append("@function_tool")
            signature = ", ".join(params)
            lines.append(f"def {func}({signature}) -> str:")
            lines.append(f'    """{description or name}"""')
            lines.append(f'    raise NotImplementedError("implement {name}")')
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _model_id(agent) -> str | None:
        if not agent.model:
            return None
        model = agent.model_by_name(agent.model)
        value = model.model if model else agent.model
        return value if value and not value.startswith("your-") else None

    @staticmethod
    def _tools(agent) -> list[dict]:
        return [
            {
                "name": tool.metadata.name,
                "description": tool.metadata.description or "",
                "parameters": tool.input_schema,
            }
            for tool in agent.tools
        ]


class OpenAIChatProjector(Projector):
    name = "openai-chat"
    description = (
        "OpenAI Chat Completions request scaffold: model, system message and "
        "function tool schemas."
    )

    def project(self, compiled: CompiledAgent) -> ProjectionResult:
        agent = compiled.definition
        warnings: list[Diagnostic] = []
        model_id = OpenAIAgentsProjector._model_id(agent) or "your-model-id"
        if model_id == "your-model-id":
            warnings.append(
                Diagnostic(
                    "DTA504",
                    "no concrete model id was found; placeholders are emitted",
                    Severity.WARNING,
                )
            )
        payload: dict = {
            "model": model_id,
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.metadata.name,
                        "description": tool.metadata.description or "",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in agent.tools
            ],
        }
        if agent.instructions and agent.instructions.text.strip():
            payload["messages"].append(
                {"role": "system", "content": agent.instructions.text.strip()}
            )
        return ProjectionResult(
            target=self.name,
            files=(
                ProjectedFile.text(
                    "openai-chat.json",
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    media_type="application/json",
                ),
            ),
            warnings=tuple(warnings),
        )
