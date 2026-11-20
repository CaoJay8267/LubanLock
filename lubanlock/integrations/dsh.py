"""Import a DeepSeek Harness agent preset into LubanLock.

Supports the documented legacy preset directory (``preset.yml`` +
``agent.cordis.yml``) and the newer bundle patch (``cordis.patch.yml``).
Persona prefixes become instructions; ``@deepseek-ai/dsh-tool-*`` plugins become
generic tool declarations.  Unmapped plugins are reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from lubanlock.core.errors import UsageError
from lubanlock.core.paths import AGENTS_FILE, resource_agent_root
from lubanlock.core.utils import safe_load_yaml, slugify

_AGENT_PRESET = "@deepseek-ai/dsh-agent-preset"
_PERSONA = "@deepseek-ai/dsh-persona"
_TOOL_PREFIX = "@deepseek-ai/dsh-tool-"


@dataclass(frozen=True)
class DshImportReport:
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


def import_dsh(
    source: str | Path,
    *,
    out: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
    resource_root: str | Path = "resource",
) -> DshImportReport:
    root = Path(source)
    if not root.exists():
        raise UsageError(f"source does not exist: {root}")
    directory = root if root.is_dir() else root.parent
    config, legacy_name, legacy_description = _read_preset(directory, root)
    preset_id = slugify(name or legacy_name or str(config.get("id") or directory.name))
    project_root = (
        Path(out)
        if out
        else resource_agent_root(preset_id, resource_root=resource_root)
    )
    if project_root.exists() and any(project_root.iterdir()) and not force:
        raise UsageError(
            f"destination is not empty: {project_root}; pass --force to overwrite"
        )
    spec_dir = project_root / ".agents"
    spec_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    warnings: list[str] = []

    plugins = config.get("plugins")
    plugins = plugins if isinstance(plugins, list) else _legacy_plugins(directory)
    persona = _find_persona(plugins)
    instructions = persona or f"# {legacy_name or preset_id}\n\nImported from DeepSeek Harness.\n"
    (project_root / AGENTS_FILE).write_text(instructions, encoding="utf-8")
    written.append(project_root / AGENTS_FILE)

    metadata = {
        "name": preset_id,
        "title": str(config.get("name") or legacy_name or preset_id),
        "description": str(
            config.get("description") or legacy_description or "Imported from DeepSeek Harness."
        ),
        "version": "0.1.0",
    }
    agent_yaml = {
        "apiVersion": "lubanlock/v1alpha1",
        "kind": "Agent",
        "metadata": metadata,
        "spec": {},
    }
    _write_yaml(spec_dir / "agent.yaml", agent_yaml, written)

    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        plugin_name = str(plugin.get("name") or "")
        if plugin_name == _PERSONA or plugin.get("id") == "persona":
            continue
        if plugin_name.startswith(_TOOL_PREFIX):
            tool_name = slugify(plugin_name[len(_TOOL_PREFIX) :])
            _write_tool(spec_dir, tool_name, plugin_name, written)
        elif plugin_name and plugin_name != _AGENT_PRESET:
            warnings.append(f"unmapped DSH plugin {plugin_name!r}")
    return DshImportReport(
        source=str(root),
        out=str(project_root),
        name=preset_id,
        files=tuple(str(path) for path in written),
        warnings=tuple(warnings),
    )


def looks_like_dsh(path: Path) -> bool:
    if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}:
        try:
            data = safe_load_yaml(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return False
        return _contains_agent_preset(data)
    if not path.is_dir():
        return False
    if (path / "preset.yml").is_file() or (path / "agent.cordis.yml").is_file():
        return True
    patch = path / "cordis.patch.yml"
    if patch.is_file():
        try:
            data = safe_load_yaml(patch.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return False
        return _contains_agent_preset(data)
    return False


def _contains_agent_preset(data: object) -> bool:
    if isinstance(data, dict):
        if data.get("name") == _AGENT_PRESET:
            return True
        if isinstance(data.get("insert"), list):
            return _contains_agent_preset(data["insert"])
    if isinstance(data, list):
        return any(_contains_agent_preset(item) for item in data)
    return False


def _read_yaml(path: Path):
    try:
        return safe_load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc


def _read_preset(directory: Path, source: Path) -> tuple[dict, str | None, str | None]:
    preset_file = directory / "preset.yml"
    legacy_name: str | None = None
    legacy_description: str | None = None
    if preset_file.is_file():
        display = _read_yaml(preset_file)
        if isinstance(display, dict):
            legacy_name = display.get("name")
            legacy_description = display.get("description")
    for candidate in (directory / "cordis.patch.yml", source if source.is_file() else None):
        if candidate is None or not candidate.is_file():
            continue
        data = _read_yaml(candidate)
        config = _find_preset_config(data)
        if config is not None:
            return config, legacy_name, legacy_description
    if preset_file.is_file() or (directory / "agent.cordis.yml").is_file():
        return {}, legacy_name, legacy_description
    raise UsageError(
        f"no DeepSeek Harness agent preset found at {source} "
        "(expected preset.yml/agent.cordis.yml or a cordis.patch.yml with "
        f"{_AGENT_PRESET})"
    )


def _find_preset_config(data: object) -> dict | None:
    if isinstance(data, dict):
        if data.get("name") == _AGENT_PRESET and isinstance(data.get("config"), dict):
            return data["config"]
        if isinstance(data.get("insert"), list):
            for item in data["insert"]:
                found = _find_preset_config(item)
                if found is not None:
                    return found
    if isinstance(data, list):
        for item in data:
            found = _find_preset_config(item)
            if found is not None:
                return found
    return None


def _legacy_plugins(directory: Path) -> list:
    path = directory / "agent.cordis.yml"
    if not path.is_file():
        return []
    data = _read_yaml(path)
    return data if isinstance(data, list) else []


def _find_persona(plugins: list) -> str | None:
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        if plugin.get("name") != _PERSONA and plugin.get("id") != "persona":
            continue
        config = plugin.get("config")
        if isinstance(config, dict):
            prefix = config.get("prefix")
            if isinstance(prefix, str) and prefix.strip():
                return prefix.strip()
    return None


def _write_yaml(path: Path, data: dict, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )
    written.append(path)


def _write_tool(spec_dir: Path, name: str, plugin_name: str, written: list[Path]) -> None:
    _write_yaml(
        spec_dir / "tools" / f"{name}.yaml",
        {
            "apiVersion": "lubanlock/v1alpha1",
            "kind": "Tool",
            "metadata": {
                "name": name,
                "description": f"Imported DSH tool plugin {plugin_name}; provide a real input schema.",
            },
            "spec": {
                "input_schema": {"type": "object", "additionalProperties": True},
                "source": {"kind": "external", "ref": f"dsh:{plugin_name}"},
            },
        },
        written,
    )


__all__ = ["DshImportReport", "import_dsh", "looks_like_dsh"]
