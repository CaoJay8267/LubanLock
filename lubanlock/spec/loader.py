"""Discovery and parsing of an agent project.

Canonical layout (v0.2) is ``<project>/AGENTS.md`` plus ``<project>/.agents/``.
The legacy ``.agent/`` directory and ``subagents/`` folder are accepted with a
deprecation warning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lubanlock.core.errors import Diagnostic, Severity
from lubanlock.core.paths import (
    AGENTS_FILE,
    LEGACY_INSTRUCTIONS_FILE,
    LEGACY_SUBAGENTS_DIR,
    AgentLayout,
    resolve_agent_layout,
    resolve_agent_root,
)
from lubanlock.core.utils import (
    FrontMatterError,
    iter_all_files,
    iter_files,
    read_text,
    safe_load_yaml,
    split_front_matter,
    to_posix,
)
from lubanlock.spec.schema import (
    KNOWLEDGE_SCHEMA,
    MODEL_SCHEMA,
    SKILL_SCHEMA,
    SUBAGENT_SCHEMA,
    TASK_SCHEMA,
    TOOL_SCHEMA,
    WORKFLOW_SCHEMA,
)

#: kind -> (directory name, accepted suffixes, schema)
STRUCTURED_KINDS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "tool": ("tools", (".yaml", ".yml", ".json"), TOOL_SCHEMA),
    "model": ("models", (".yaml", ".yml", ".json"), MODEL_SCHEMA),
    "workflow": ("workflows", (".yaml", ".yml", ".json"), WORKFLOW_SCHEMA),
    "task": ("tasks", (".yaml", ".yml", ".json"), TASK_SCHEMA),
}

ALL_KINDS = ("knowledge", "skill", "subagent", "tool", "model", "workflow", "task")

_KNOWN_DIRS = {
    "knowledge",
    "skills",
    "agents",
    LEGACY_SUBAGENTS_DIR,
    "tools",
    "models",
    "workflows",
    "tasks",
    "tests",
}
_KNOWN_FILES = {
    "agent.yaml",
    "agent.yml",
    "mcp.json",
    "audit.yaml",
    "lubanlock.lock",
    LEGACY_INSTRUCTIONS_FILE,
}


@dataclass(frozen=True)
class RawEntity:
    """One entity file as read from disk, before schema validation."""

    kind: str
    schema: str
    rel: str  # relative to the spec directory (stable, used in IR)
    display: str  # project-relative (used in diagnostics)
    path: Path
    data: object
    body: str | None = None
    extra_files: tuple[str, ...] = ()
    source_root: Path | None = None


@dataclass
class AgentSource:
    """Everything discovered for one agent project."""

    layout: AgentLayout
    manifest: object | None
    manifest_rel: str
    manifest_path: Path | None
    instructions: tuple[str, str] | None
    mcp: object | None = None
    mcp_rel: str | None = None
    entities: dict[str, tuple[RawEntity, ...]] = field(default_factory=dict)
    files: tuple[str, ...] = ()
    library_roots: tuple[tuple[str, Path], ...] = ()
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.layout.project_root

    @property
    def spec_dir(self) -> Path:
        return self.layout.spec_dir

    @property
    def spec_prefix(self) -> str:
        return self.layout.spec_prefix

    def entities_of(self, kind: str) -> tuple[RawEntity, ...]:
        return self.entities.get(kind, ())

    def add(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)


def load_source(path: str | Path) -> AgentSource:
    """Read an agent project.  Root errors raise :class:`LoadError`."""

    layout = resolve_agent_layout(path)
    diagnostics: list[Diagnostic] = []
    files: list[str] = []

    if layout.legacy_spec_dir:
        diagnostics.append(
            Diagnostic(
                "DTA910",
                "'.agent/' is deprecated; rename it to '.agents/'",
                Severity.WARNING,
                path=layout.spec_prefix,
            )
        )

    manifest_rel, manifest, manifest_path = _load_manifest(
        layout, diagnostics
    )
    if manifest_rel:
        files.append(manifest_rel)

    instructions = _load_instructions(layout, diagnostics, files)
    mcp, mcp_rel = _load_mcp(layout, diagnostics, files)

    entities: dict[str, tuple[RawEntity, ...]] = {}
    entities["knowledge"] = tuple(_load_knowledge(layout, diagnostics, files))
    entities["skill"] = tuple(_load_skills(layout, diagnostics, files))
    entities["subagent"] = tuple(_load_subagents(layout, diagnostics, files))
    for kind, (directory, suffixes, schema) in STRUCTURED_KINDS.items():
        entities[kind] = tuple(
            _load_structured(layout, kind, directory, suffixes, schema, diagnostics, files)
        )

    _warn_ignored(layout, diagnostics)

    return AgentSource(
        layout=layout,
        manifest=manifest,
        manifest_rel=manifest_rel,
        manifest_path=manifest_path,
        instructions=instructions,
        mcp=mcp,
        mcp_rel=mcp_rel,
        entities=entities,
        files=tuple(sorted(set(files))),
        diagnostics=diagnostics,
    )


def _display(layout: AgentLayout, rel: str) -> str:
    return f"{layout.spec_prefix}/{rel}" if layout.spec_prefix else rel


def _load_manifest(
    layout: AgentLayout, diagnostics: list[Diagnostic]
) -> tuple[str, object | None, Path | None]:
    spec_dir = layout.spec_dir
    candidates = [
        name for name in ("agent.yaml", "agent.yml") if (spec_dir / name).is_file()
    ]
    if not candidates:
        diagnostics.append(
            Diagnostic(
                "DTA101",
                f"missing agent manifest: expected {layout.spec_prefix}/agent.yaml",
                path=layout.spec_prefix,
                hint="run 'lubanlock init' to create a minimal agent skeleton",
            )
        )
        return "", None, None
    if len(candidates) > 1:
        diagnostics.append(
            Diagnostic(
                "DTA110",
                "both agent.yaml and agent.yml exist; keep exactly one",
                path=_display(layout, "agent.yaml"),
            )
        )
    name = candidates[0]
    display = _display(layout, name)
    text = _read_text(spec_dir / name, display, diagnostics)
    if text is None:
        return display, None, spec_dir / name
    data = _parse_yaml(text, display, diagnostics)
    return display, data, spec_dir / name


def _load_instructions(
    layout: AgentLayout, diagnostics: list[Diagnostic], files: list[str]
) -> tuple[str, str] | None:
    root = layout.project_root
    spec_dir = layout.spec_dir
    agents_md = root / AGENTS_FILE
    legacy = spec_dir / LEGACY_INSTRUCTIONS_FILE
    if agents_md.is_file():
        text = _read_text(agents_md, AGENTS_FILE, diagnostics)
        if text is not None:
            files.append(AGENTS_FILE)
            if legacy.is_file():
                diagnostics.append(
                    Diagnostic(
                        "DTA911",
                        f"{layout.spec_prefix}/{LEGACY_INSTRUCTIONS_FILE} is ignored; "
                        f"{AGENTS_FILE} takes precedence",
                        Severity.WARNING,
                        path=_display(layout, LEGACY_INSTRUCTIONS_FILE),
                    )
                )
            return AGENTS_FILE, text
    if legacy.is_file():
        text = _read_text(
            legacy, _display(layout, LEGACY_INSTRUCTIONS_FILE), diagnostics
        )
        if text is not None:
            files.append(_display(layout, LEGACY_INSTRUCTIONS_FILE))
            diagnostics.append(
                Diagnostic(
                    "DTA911",
                    f"{layout.spec_prefix}/{LEGACY_INSTRUCTIONS_FILE} is deprecated; "
                    f"use {AGENTS_FILE} at the project root",
                    Severity.WARNING,
                    path=_display(layout, LEGACY_INSTRUCTIONS_FILE),
                )
            )
            return LEGACY_INSTRUCTIONS_FILE, text
    return None


def _load_mcp(
    layout: AgentLayout, diagnostics: list[Diagnostic], files: list[str]
) -> tuple[object | None, str | None]:
    path = layout.spec_dir / "mcp.json"
    if not path.is_file():
        return None, None
    rel = _display(layout, "mcp.json")
    files.append(rel)
    text = _read_text(path, rel, diagnostics)
    if text is None:
        return None, rel
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        diagnostics.append(
            Diagnostic(
                "DTA103",
                f"invalid JSON in mcp.json (line {exc.lineno}, column {exc.colno}): {exc.msg}",
                path=rel,
            )
        )
        return None, rel
    return data, rel


def _load_structured(
    layout: AgentLayout,
    kind: str,
    directory: str,
    suffixes: tuple[str, ...],
    schema: str,
    diagnostics: list[Diagnostic],
    files: list[str],
) -> list[RawEntity]:
    base = layout.spec_dir / directory
    if not base.is_dir():
        return []
    entities: list[RawEntity] = []
    for path in iter_files(base, suffixes):
        rel = to_posix(path.relative_to(layout.spec_dir))
        display = _display(layout, rel)
        files.append(display)
        if path.suffix.lower() == ".json":
            data = _parse_json(path, display, diagnostics)
        else:
            text = _read_text(path, display, diagnostics)
            if text is None:
                continue
            data = _parse_yaml(text, display, diagnostics)
        if data is None:
            continue
        entities.append(
            RawEntity(
                kind=kind,
                schema=schema,
                rel=rel,
                display=display,
                path=path,
                data=data,
            )
        )
    for path in _unexpected(base, suffixes):
        rel = to_posix(path.relative_to(layout.spec_dir))
        diagnostics.append(
            Diagnostic(
                "DTA110",
                f"ignored unsupported file in {directory}/: {path.name}",
                path=_display(layout, rel),
                hint=f"accepted suffixes: {', '.join(suffixes)}",
            )
        )
    return entities


def _load_knowledge(
    layout: AgentLayout, diagnostics: list[Diagnostic], files: list[str]
) -> list[RawEntity]:
    base = layout.spec_dir / "knowledge"
    entities: list[RawEntity] = []
    for path in iter_files(base, (".md", ".markdown", ".txt")):
        rel = to_posix(path.relative_to(layout.spec_dir))
        display = _display(layout, rel)
        files.append(display)
        text = _read_text(path, display, diagnostics)
        if text is None:
            continue
        if path.suffix.lower() == ".txt":
            data: object = {}
            body = text
        else:
            try:
                document = split_front_matter(text)
            except FrontMatterError as exc:
                diagnostics.append(
                    Diagnostic("DTA104", str(exc), path=display, hint="check the '---' fences")
                )
                continue
            data = document.front_matter if document.front_matter is not None else {}
            body = document.body
        entities.append(
            RawEntity(
                kind="knowledge",
                schema=KNOWLEDGE_SCHEMA,
                rel=rel,
                display=display,
                path=path,
                data=data,
                body=body.strip(),
            )
        )
    _warn_ignored_named(base, layout, diagnostics, {".md", ".markdown", ".txt"}, "knowledge")
    return entities


def _load_skills(
    layout: AgentLayout, diagnostics: list[Diagnostic], files: list[str]
) -> list[RawEntity]:
    base = layout.spec_dir / "skills"
    if not base.is_dir():
        return []
    entities: list[RawEntity] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
                diagnostics.append(
                    Diagnostic(
                        "DTA110",
                        f"skill directory {child.name!r} has no SKILL.md",
                        path=_display(layout, f"skills/{child.name}"),
                        hint="Agent Skills require a SKILL.md file in each skill directory",
                    )
                )
                continue
            rel = f"skills/{child.name}/SKILL.md"
            display = _display(layout, rel)
            text = _read_text(skill_file, display, diagnostics)
            if text is None:
                continue
            try:
                document = split_front_matter(text)
            except FrontMatterError as exc:
                diagnostics.append(
                    Diagnostic("DTA104", str(exc), path=display, hint="check the '---' fences")
                )
                continue
            data = document.front_matter if document.front_matter is not None else {}
            extra = tuple(
                to_posix(path.relative_to(layout.spec_dir))
                for path in iter_all_files(child)
                if path != skill_file
            )
            files.append(display)
            files.extend(_display(layout, item) for item in extra)
            entities.append(
                RawEntity(
                    kind="skill",
                    schema=SKILL_SCHEMA,
                    rel=rel,
                    display=display,
                    path=skill_file,
                    data=data,
                    body=document.body.strip(),
                    extra_files=extra,
                )
            )
        elif child.is_file() and not child.name.startswith("."):
            diagnostics.append(
                Diagnostic(
                    "DTA110",
                    f"ignored {child.name!r} in skills/: skills must be directories",
                    path=_display(layout, f"skills/{child.name}"),
                    hint="Agent Skills layout: skills/<name>/SKILL.md",
                )
            )
    return entities


def _load_subagents(
    layout: AgentLayout, diagnostics: list[Diagnostic], files: list[str]
) -> list[RawEntity]:
    spec_dir = layout.spec_dir
    canonical = spec_dir / "agents"
    legacy = spec_dir / LEGACY_SUBAGENTS_DIR
    if canonical.is_dir() and legacy.is_dir():
        diagnostics.append(
            Diagnostic(
                "DTA110",
                "both agents/ and subagents/ exist; only agents/ is loaded",
                path=layout.spec_prefix,
            )
        )
    base = canonical if canonical.is_dir() else legacy
    if not base.is_dir():
        return []
    if base is legacy:
        diagnostics.append(
            Diagnostic(
                "DTA910",
                "subagents/ is deprecated; rename it to agents/",
                Severity.WARNING,
                path=layout.spec_prefix,
            )
        )
    entities: list[RawEntity] = []
    for path in iter_files(base, (".md", ".markdown")):
        rel = to_posix(path.relative_to(spec_dir))
        display = _display(layout, rel)
        text = _read_text(path, display, diagnostics)
        if text is None:
            continue
        try:
            document = split_front_matter(text)
        except FrontMatterError as exc:
            diagnostics.append(
                Diagnostic("DTA104", str(exc), path=display, hint="check the '---' fences")
            )
            continue
        if document.front_matter is None:
            diagnostics.append(
                Diagnostic(
                    "DTA104",
                    "subagent file requires YAML front matter with name and description",
                    path=display,
                )
            )
            continue
        files.append(display)
        entities.append(
            RawEntity(
                kind="subagent",
                schema=SUBAGENT_SCHEMA,
                rel=rel,
                display=display,
                path=path,
                data=document.front_matter,
                body=document.body.strip(),
            )
        )
    for child in sorted(base.iterdir()):
        if child.is_dir():
            diagnostics.append(
                Diagnostic(
                    "DTA110",
                    f"ignored subagent directory {child.name!r}",
                    path=_display(layout, to_posix(child.relative_to(spec_dir))),
                    hint="v1alpha1 supports lightweight subagents: agents/<name>.md",
                )
            )
    return entities


def _warn_ignored(layout: AgentLayout, diagnostics: list[Diagnostic]) -> None:
    spec_dir = layout.spec_dir
    for child in sorted(spec_dir.iterdir()):
        if child.name.startswith(".") or child.name in _KNOWN_DIRS or child.name in _KNOWN_FILES:
            continue
        if child.is_dir():
            continue
        diagnostics.append(
            Diagnostic(
                "DTA110",
                f"ignored unrecognized file in {layout.spec_prefix}/: {child.name}",
                path=_display(layout, child.name),
            )
        )


def _warn_ignored_named(
    base: Path,
    layout: AgentLayout,
    diagnostics: list[Diagnostic],
    suffixes: set[str],
    directory: str,
) -> None:
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if "__pycache__" in path.parts or path.suffix.lower() in suffixes:
            continue
        diagnostics.append(
            Diagnostic(
                "DTA110",
                f"ignored unsupported file in {directory}/: {path.name}",
                path=_display(layout, to_posix(path.relative_to(layout.spec_dir))),
            )
        )


def _unexpected(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in suffixes:
            found.append(path)
    return found


def _read_text(path: Path, display: str, diagnostics: list[Diagnostic]) -> str | None:
    try:
        return read_text(path)
    except FileNotFoundError:
        diagnostics.append(Diagnostic("DTA102", "file not found", path=display))
    except ValueError as exc:
        diagnostics.append(Diagnostic("DTA105", str(exc), path=display))
    except UnicodeDecodeError as exc:
        diagnostics.append(Diagnostic("DTA106", f"file is not valid UTF-8: {exc}", path=display))
    except OSError as exc:
        diagnostics.append(Diagnostic("DTA102", f"cannot read file: {exc}", path=display))
    return None


def _parse_yaml(text: str, display: str, diagnostics: list[Diagnostic]) -> object | None:
    try:
        return safe_load_yaml(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = ""
        if mark is not None:
            location = f" (line {mark.line + 1}, column {mark.column + 1})"
        problem = getattr(exc, "problem", None) or str(exc)
        diagnostics.append(Diagnostic("DTA103", f"invalid YAML{location}: {problem}", path=display))
        return None


def _parse_json(path: Path, display: str, diagnostics: list[Diagnostic]) -> object | None:
    text = _read_text(path, display, diagnostics)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        diagnostics.append(
            Diagnostic(
                "DTA103",
                f"invalid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}",
                path=display,
            )
        )
        return None


__all__ = [
    "STRUCTURED_KINDS",
    "AgentLayout",
    "AgentSource",
    "RawEntity",
    "load_source",
    "resolve_agent_layout",
    "resolve_agent_root",
]
