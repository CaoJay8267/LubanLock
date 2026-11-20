"""Minimal MCP client: connect, list tools, import schemas.

Only stdio and basic HTTP JSON-RPC transports are implemented.  SSE/WebSocket
servers are reported as unsupported instead of being guessed at.  Commands are
executed without a shell, with a timeout and output limits.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import yaml

from dotagent.core.errors import PackageError, Severity
from dotagent.core.models import AgentDefinition
from dotagent.core.utils import slugify
from dotagent.spec.schema import lint_json_schema

PROTOCOL_VERSION = "2024-11-05"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_TOOLS = 200


@dataclass(frozen=True)
class McpToolInfo:
    name: str
    description: str
    input_schema: dict
    output_schema: dict | None = None
    annotations: dict = field(default_factory=dict)


@dataclass(frozen=True)
class McpDiscovery:
    server: str
    transport: str
    tools: tuple[McpToolInfo, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpImportReport:
    discoveries: tuple[McpDiscovery, ...]
    files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "servers": [
                {
                    "name": discovery.server,
                    "transport": discovery.transport,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in discovery.tools
                    ],
                    "warnings": list(discovery.warnings),
                }
                for discovery in self.discoveries
            ],
            "files": list(self.files),
            "warnings": list(self.warnings),
        }


def _expand(value: str) -> str:
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

    def replace(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        return os.environ.get(name, default or "")

    return pattern.sub(replace, value)


class _StdioTransport:
    def __init__(self, config: dict, timeout: float):
        self.command = _expand(str(config.get("command", "")))
        self.args = [_expand(str(item)) for item in config.get("args") or ()]
        self.env = {**_os_environ(), **{str(k): _expand(str(v)) for k, v in (config.get("env") or {}).items()}}
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._counter = 0

    def open(self) -> None:
        if not self.command:
            raise PackageError("stdio MCP server has no command")
        try:
            self.process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise PackageError(f"cannot start MCP server {self.command!r}: {exc}") from exc
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            if len(line) > MAX_RESPONSE_BYTES:
                continue
            self._queue.put(line)

    def _write(self, payload: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise PackageError("MCP server is not running")
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PackageError(f"cannot write to MCP server: {exc}") from exc

    def request(self, method: str, params: dict | None = None) -> dict:
        self._counter += 1
        request_id = self._counter
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        deadline = self.timeout
        while True:
            try:
                line = self._queue.get(timeout=deadline)
            except queue.Empty as exc:
                raise PackageError(f"MCP request {method!r} timed out") from exc
            if self.process is not None and self.process.poll() is not None:
                raise PackageError(f"MCP server exited with code {self.process.returncode}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise PackageError(
                    f"MCP request {method!r} failed: {error.get('message', error)}"
                )
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except Exception:  # noqa: BLE001 - best effort
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001, S110 - best effort
                pass
        for stream in (self.process.stdin, self.process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _os_environ() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()}


class _HttpTransport:
    def __init__(self, config: dict, timeout: float):
        self.url = _expand(str(config.get("url", "")))
        self.headers = {
            str(k): _expand(str(v)) for k, v in (config.get("headers") or {}).items()
        }
        self.timeout = timeout
        self.session_id: str | None = None
        self._counter = 0

    def open(self) -> None:
        if not self.url:
            raise PackageError("http MCP server has no url")

    def _post(self, payload: dict) -> tuple[dict | None, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PackageError("MCP response exceeds the size limit")
                return _decode_body(body, response.headers.get_content_type()), session or ""
        except urllib.error.HTTPError as exc:
            raise PackageError(f"MCP HTTP error {exc.code} for {self.url}") from exc
        except urllib.error.URLError as exc:
            raise PackageError(f"cannot reach MCP server {self.url}: {exc.reason}") from exc

    def request(self, method: str, params: dict | None = None) -> dict:
        self._counter += 1
        request_id = self._counter
        message, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        if message is None:
            raise PackageError(f"MCP request {method!r} returned no JSON")
        if "error" in message:
            error = message["error"]
            raise PackageError(f"MCP request {method!r} failed: {error.get('message', error)}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except PackageError:
            pass

    def close(self) -> None:
        return None


def _decode_body(body: bytes, content_type: str) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                return message
        return None
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def discover_server(
    name: str, config: dict, *, timeout: float = 10.0
) -> McpDiscovery:
    """Connect to one MCP server and return its tool list."""

    warnings: list[str] = []
    transport_type = config.get("type") or ("stdio" if config.get("command") else "http")
    if transport_type == "stdio":
        transport: _StdioTransport | _HttpTransport = _StdioTransport(config, timeout)
    elif transport_type in {"http", "streamable-http"}:
        transport = _HttpTransport(config, timeout)
    else:
        return McpDiscovery(
            server=name,
            transport=str(transport_type),
            tools=(),
            warnings=(f"unsupported MCP transport {transport_type!r}; use stdio or http",),
        )
    tools: list[McpToolInfo] = []
    try:
        transport.open()
        transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "dotagent", "version": "0.3.0"},
            },
        )
        transport.notify("notifications/initialized")
        cursor: str | None = None
        for _ in range(10):
            params = {"cursor": cursor} if cursor else {}
            result = transport.request("tools/list", params)
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                break
            for raw in raw_tools:
                if not isinstance(raw, dict):
                    continue
                tool_name = raw.get("name")
                if not isinstance(tool_name, str) or not tool_name:
                    continue
                schema = raw.get("inputSchema")
                if not isinstance(schema, dict):
                    warnings.append(f"tool {tool_name!r} has no object inputSchema; skipped")
                    continue
                annotations = raw.get("annotations")
                tools.append(
                    McpToolInfo(
                        name=tool_name,
                        description=str(raw.get("description") or ""),
                        input_schema=schema,
                        output_schema=raw.get("outputSchema")
                        if isinstance(raw.get("outputSchema"), dict)
                        else None,
                        annotations=annotations if isinstance(annotations, dict) else {},
                    )
                )
                if len(tools) >= MAX_TOOLS:
                    warnings.append(f"stopped after {MAX_TOOLS} tools")
                    break
            cursor = result.get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
    finally:
        transport.close()
    return McpDiscovery(
        server=name,
        transport=str(transport_type),
        tools=tuple(tools),
        warnings=tuple(warnings),
    )


def discover_agent(
    definition: AgentDefinition,
    *,
    servers: tuple[str, ...] = (),
    timeout: float = 10.0,
) -> McpImportReport:
    selected = [
        (name, config)
        for name, config in definition.mcp
        if not servers or name in servers
    ]
    if not selected:
        return McpImportReport(discoveries=(), warnings=("agent declares no MCP servers",))
    discoveries: list[McpDiscovery] = []
    warnings: list[str] = []
    for name, config in selected:
        try:
            discoveries.append(discover_server(name, config, timeout=timeout))
        except PackageError as exc:
            warnings.append(f"{name}: {exc}")
    return McpImportReport(discoveries=tuple(discoveries), warnings=tuple(warnings))


def import_tools(
    definition: AgentDefinition, report: McpImportReport, *, overwrite: bool = False
) -> McpImportReport:
    """Write discovered MCP tools as DotAgent tool declarations."""

    files: list[str] = []
    warnings = list(report.warnings)
    existing = {tool.metadata.name for tool in definition.tools}
    for discovery in report.discoveries:
        for tool in discovery.tools:
            tool_name = slugify(f"{discovery.server}-{tool.name}")
            if tool_name in existing and not overwrite:
                warnings.append(f"tool {tool_name!r} already exists; skipped")
                continue
            lint = lint_json_schema(tool.input_schema, path=f"mcp:{discovery.server}/{tool.name}")
            if any(d.severity is Severity.ERROR for d in lint):
                warnings.append(
                    f"MCP tool {discovery.server}/{tool.name} uses unsupported schema keywords; skipped"
                )
                continue
            annotations: dict[str, bool] = {}
            if tool.annotations.get("readOnlyHint") is not None:
                annotations["read_only"] = bool(tool.annotations["readOnlyHint"])
            if tool.annotations.get("destructiveHint") is not None:
                annotations["destructive"] = bool(tool.annotations["destructiveHint"])
            if tool.annotations.get("idempotentHint") is not None:
                annotations["idempotent"] = bool(tool.annotations["idempotentHint"])
            document = {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Tool",
                "metadata": {
                    "name": tool_name,
                    "description": tool.description or f"MCP tool {discovery.server}/{tool.name}",
                },
                "spec": {
                    "input_schema": tool.input_schema,
                    "source": {"kind": "mcp", "ref": f"{discovery.server}/{tool.name}"},
                },
            }
            if tool.output_schema is not None:
                document["spec"]["output_schema"] = tool.output_schema
            if annotations:
                document["spec"]["annotations"] = annotations
            path = definition.spec_dir / "tools" / f"{tool_name}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=1000),
                encoding="utf-8",
            )
            files.append(str(path))
            existing.add(tool_name)
    return McpImportReport(
        discoveries=report.discoveries,
        files=tuple(files),
        warnings=tuple(warnings),
    )


__all__ = [
    "McpDiscovery",
    "McpImportReport",
    "McpToolInfo",
    "discover_agent",
    "discover_server",
    "import_tools",
]
