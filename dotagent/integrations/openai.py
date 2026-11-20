"""Import OpenAI projections back into DotAgent (no code execution).

Supported inputs:

* ``openai-chat.json`` (model, messages, tools),
* an ``agent.py`` scaffold whose ``Agent(...)`` call uses literal arguments,
* a directory containing either of the above.

Python is parsed with :mod:`ast`; it is never imported or executed.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from dotagent.core.errors import UsageError
from dotagent.core.paths import AGENTS_FILE, resource_agent_root
from dotagent.core.utils import slugify


@dataclass(frozen=True)
class OpenAIImportReport:
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


def looks_like_openai(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.is_file():
        if candidate.suffix.lower() == ".json":
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return False
            return isinstance(data, dict) and "messages" in data
        return candidate.name == "agent.py"
    if candidate.is_dir():
        return (candidate / "openai-chat.json").is_file() or (
            candidate / "agent.py"
        ).is_file() or (candidate / "openai" / "agent.py").is_file()
    return False


def import_openai(
    source: str | Path,
    *,
    out: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
    resource_root: str | Path = "resource",
) -> OpenAIImportReport:
    root = Path(source)
    if not root.exists():
        raise UsageError(f"source does not exist: {root}")
    instructions = ""
    model: str | None = None
    tools: list[dict] = []
    warnings: list[str] = []
    inferred_name = name or root.stem

    chat = _find_chat_file(root)
    agent_py = _find_agent_py(root)
    if chat is not None:
        payload = _read_json(chat)
        messages = payload.get("messages")
        if isinstance(messages, list):
            instructions = "\n\n".join(
                str(message.get("content", ""))
                for message in messages
                if isinstance(message, dict) and message.get("role") == "system"
            )
        if isinstance(payload.get("model"), str):
            model = payload["model"]
        for item in payload.get("tools") or ():
            if not isinstance(item, dict) or item.get("type") != "function":
                continue
            function = item.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                tools.append(function)
    elif agent_py is not None:
        parsed = _parse_agent_py(agent_py)
        instructions = parsed.get("instructions") or ""
        model = parsed.get("model")
        inferred_name = name or parsed.get("name") or root.stem
        tools_file = agent_py.parent / "tools.json"
        if tools_file.is_file():
            data = _read_json_value(tools_file)
            if isinstance(data, list):
                tools = [item for item in data if isinstance(item, dict)]
        warnings.extend(parsed.get("warnings") or [])
    else:
        raise UsageError(f"no OpenAI projection found at {root}")

    agent_name = slugify(inferred_name)
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
    (project_root / AGENTS_FILE).write_text(
        (instructions or f"# {agent_name}\n\nImported from OpenAI.") + "\n",
        encoding="utf-8",
    )
    written.append(project_root / AGENTS_FILE)

    metadata = {
        "name": agent_name,
        "title": agent_name,
        "description": "Imported from an OpenAI projection.",
        "version": "0.1.0",
    }
    spec: dict = {}
    if model:
        spec["model"] = "default"
        _write_yaml(
            spec_dir / "models" / "default.yaml",
            {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Model",
                "metadata": {"name": "default", "description": "Imported OpenAI model."},
                "spec": {"model": model, "provider": "openai"},
            },
            written,
        )
    _write_yaml(
        spec_dir / "agent.yaml",
        {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Agent",
            "metadata": metadata,
            "spec": spec,
        },
        written,
    )
    for function in tools:
        _write_tool(spec_dir, function, written, warnings)
    return OpenAIImportReport(
        source=str(root),
        out=str(project_root),
        name=agent_name,
        files=tuple(str(path) for path in written),
        warnings=tuple(warnings),
    )


def _find_chat_file(root: Path) -> Path | None:
    if root.is_file() and root.suffix.lower() == ".json":
        return root
    for candidate in (
        root / "openai-chat.json",
        root / "openai" / "openai-chat.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _find_agent_py(root: Path) -> Path | None:
    if root.is_file() and root.suffix.lower() == ".py":
        return root
    for candidate in (root / "agent.py", root / "openai" / "agent.py"):
        if candidate.is_file():
            return candidate
    return None


def _read_json(path: Path) -> dict:
    data = _read_json_value(path)
    if not isinstance(data, dict):
        raise UsageError(f"{path} does not contain a JSON object")
    return data


def _read_json_value(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc


def _parse_agent_py(path: Path) -> dict:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise UsageError(f"cannot parse {path}: {exc}") from exc
    warnings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        target = node.targets[0] if node.targets else None
        if not isinstance(target, ast.Name) or target.id != "agent":
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id != "Agent":
            continue
        values: dict = {}
        for keyword in call.keywords:
            try:
                values[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                if keyword.arg:
                    warnings.append(f"non-literal Agent argument {keyword.arg!r} ignored")
        return {
            "name": values.get("name"),
            "instructions": values.get("instructions"),
            "model": values.get("model"),
            "warnings": warnings,
        }
    raise UsageError(f"no literal Agent(...) assignment found in {path}")


def _write_yaml(path: Path, data: dict, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )
    written.append(path)


def _write_tool(
    spec_dir: Path, function: dict, written: list[Path], warnings: list[str]
) -> None:
    name = slugify(str(function.get("name") or "tool"))
    schema = function.get("parameters")
    if not isinstance(schema, dict):
        warnings.append(f"tool {name!r} has no parameters schema; skipped")
        return
    _write_yaml(
        spec_dir / "tools" / f"{name}.yaml",
        {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Tool",
            "metadata": {
                "name": name,
                "description": str(function.get("description") or ""),
            },
            "spec": {
                "input_schema": schema,
                "source": {"kind": "external", "ref": f"openai:{name}"},
            },
        },
        written,
    )


__all__ = ["OpenAIImportReport", "import_openai", "looks_like_openai"]
