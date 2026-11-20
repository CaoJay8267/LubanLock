"""Mermaid/HTML visualization of an agent's structure and workflows."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from dotagent.build.compiler import compile_agent
from dotagent.core.models import AgentDefinition, Workflow


@dataclass(frozen=True)
class VisualizationResult:
    agent: str
    out: str
    files: tuple[str, ...]
    diagrams: tuple[str, ...]


def visualize_agent(
    path: str | Path, *, out: str | Path | None = None
) -> VisualizationResult:
    compiled = compile_agent(path)
    definition = compiled.definition
    directory = (
        Path(out) if out else definition.root / "reports" / "visual"
    )
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    diagrams: list[str] = []

    overview = _overview_diagram(definition)
    overview_path = directory / "agent.mmd"
    overview_path.write_text(overview + "\n", encoding="utf-8")
    written.append(overview_path)
    diagrams.append(overview)

    workflow_dir = directory / "workflows"
    for workflow in definition.workflows:
        workflow_dir.mkdir(parents=True, exist_ok=True)
        diagram = _workflow_diagram(workflow)
        workflow_path = workflow_dir / f"{workflow.metadata.name}.mmd"
        workflow_path.write_text(diagram + "\n", encoding="utf-8")
        written.append(workflow_path)
        diagrams.append(diagram)

    index = _html_page(definition, diagrams)
    index_path = directory / "index.html"
    index_path.write_text(index, encoding="utf-8")
    written.append(index_path)

    return VisualizationResult(
        agent=definition.metadata.name,
        out=str(directory),
        files=tuple(str(path) for path in written),
        diagrams=tuple(diagrams),
    )


def _node_id(prefix: str, name: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in name)
    return f"{prefix}_{safe}"


def _overview_diagram(definition: AgentDefinition) -> str:
    lines = ["flowchart LR", f'  root["{_label(definition.metadata.name)} (agent)"]']
    groups = (
        ("knowledge", "knowledge", definition.knowledge),
        ("skill", "skill", definition.skills),
        ("agent", "subagent", definition.subagents),
        ("tool", "tool", definition.tools),
        ("model", "model", definition.models),
        ("task", "task", definition.tasks),
        ("workflow", "workflow", definition.workflows),
    )
    for prefix, kind, items in groups:
        for item in items:
            name = item.name if hasattr(item, "name") else item.metadata.name
            node = _node_id(prefix, name)
            lines.append(f'  {node}["{_label(kind)}: {_label(name)}"]')
            lines.append(f"  root --> {node}")
    for name, _config in definition.mcp:
        node = _node_id("mcp", name)
        lines.append(f'  {node}["mcp: {_label(name)}"]')
        lines.append(f"  root --> {node}")
    return "\n".join(lines)


def _workflow_diagram(workflow: Workflow) -> str:
    lines = ["flowchart TD"]
    for node in workflow.nodes:
        detail = node.kind + (f": {node.ref}" if node.ref else "")
        lines.append(f'  {node.id}["{_label(node.id)}<br/>{_label(detail)}"]')
    for edge in workflow.edges:
        label = f"|{_label(edge.label)}|" if edge.label else ""
        lines.append(f"  {edge.source} -->{label} {edge.target}")
    return "\n".join(lines)


def _label(value: str | None) -> str:
    if not value:
        return ""
    return value.replace('"', "#quot;").replace("\n", " ")


def _html_page(definition: AgentDefinition, diagrams: list[str]) -> str:
    blocks = "\n".join(
        f'<h2>Diagram {index}</h2>\n<pre class="mermaid">\n{html.escape(diagram)}\n</pre>'
        for index, diagram in enumerate(diagrams, start=1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(definition.metadata.name)} - DotAgent 可视化</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2937; }}
    h1 {{ font-size: 1.4rem; }}
    pre.mermaid {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 1rem; overflow: auto; }}
  </style>
</head>
<body>
  <h1>{html.escape(definition.metadata.name)}</h1>
  <p>由 DotAgent 生成。Mermaid 图需要浏览器加载 CDN 脚本；离线时可直接查看同目录下的 .mmd 文件。</p>
  {blocks}
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{ startOnLoad: true, theme: "default" }});
  </script>
</body>
</html>
"""


__all__ = ["VisualizationResult", "visualize_agent"]
