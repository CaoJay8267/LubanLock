"""Tests for v0.3 features: reuse, nesting, cache, certificate chains, MCP,
DSH/OpenAI projections and configurable audits."""

from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from dotagent.audit import audit_agent
from dotagent.build.cache import incremental_status, refresh_cache
from dotagent.build.compiler import compile_agent, write_compiled
from dotagent.build.lockfile import read_lock, write_lock
from dotagent.build.packaging import package_agent, verify_package
from dotagent.build.signing import (
    generate_certificate_chain,
    rotate_signing_certificate,
)
from dotagent.integrations import (
    discover_agent,
    import_dsh,
    import_tools,
    looks_like_dsh,
)
from dotagent.project import project_agent
from dotagent.spec.loader import load_source
from dotagent.spec.validator import validate_source
from dotagent.testkit.fixtures import minimal_project, tool_yaml, write_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-agent"


def _codes(diagnostics) -> list[str]:
    return [diagnostic.code for diagnostic in diagnostics]


class InheritanceTests(unittest.TestCase):
    def test_tool_extends_merges_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/tools/base.yaml"] = tool_yaml("base")
            files[".agents/tools/child.yaml"] = tool_yaml(
                "child", description="Child."
            ).replace("kind: Tool", "kind: Tool\nextends: base")
            project = write_project(Path(directory), files)
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            child = definition.tool("child")
            self.assertEqual(child.metadata.description, "Child.")
            self.assertEqual(child.input_schema["required"], ["text"])

    def test_subagent_extends_and_inherits_body(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/agents/base.md"] = (
                "---\nname: base\ndescription: Base.\ntools: [echo]\n---\nBase body.\n"
            )
            files[".agents/tools/echo.yaml"] = tool_yaml("echo")
            files[".agents/agents/child.md"] = (
                "---\nname: child\ndescription: Child.\nextends: base\n---\n\n"
            )
            project = write_project(Path(directory), files)
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            child = definition.subagent("child")
            self.assertEqual(child.tools, ("echo",))
            self.assertEqual(child.instructions, "Base body.")

    def test_extends_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/tools/a.yaml"] = tool_yaml("a").replace(
                "kind: Tool", "kind: Tool\nextends: b"
            )
            files[".agents/tools/b.yaml"] = tool_yaml("b").replace(
                "kind: Tool", "kind: Tool\nextends: a"
            )
            project = write_project(Path(directory), files)
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA321", _codes(result.diagnostics))

    def test_extends_missing_base(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/tools/a.yaml"] = tool_yaml("a").replace(
                "kind: Tool", "kind: Tool\nextends: missing"
            )
            project = write_project(Path(directory), files)
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA301", _codes(result.diagnostics))


class NestedAgentTests(unittest.TestCase):
    def test_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/agents/a.md"] = (
                "---\nname: a\ndescription: A.\nagents: [b]\n---\nA\n"
            )
            files[".agents/agents/b.md"] = (
                "---\nname: b\ndescription: B.\nagents: [a]\n---\nB\n"
            )
            project = write_project(Path(directory), files)
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA323", _codes(result.diagnostics))

    def test_depth_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            for index in range(10):
                child = f"a{index + 1}" if index < 9 else ""
                lines = ["---", f"name: a{index}", f"description: A{index}."]
                if child:
                    lines.append(f"agents: [{child}]")
                lines.extend(["---", f"body {index}"])
                files[f".agents/agents/a{index}.md"] = "\n".join(lines) + "\n"
            project = write_project(Path(directory), files)
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA324", _codes(result.diagnostics))


class AuditConfigTests(unittest.TestCase):
    def test_rule_disable_and_severity(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/models/default.yaml"] = files[
                ".agents/models/default.yaml"
            ].replace("model: test-model", "model: your-model-id")
            files[".agents/audit.yaml"] = (
                "apiVersion: dotagent/v1alpha1\n"
                "kind: AuditConfig\n"
                "rules:\n"
                "  AUD001: {enabled: false}\n"
                "  AUD006: {severity: error}\n"
            )
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            codes = {finding.code for finding in report.findings}
            self.assertNotIn("AUD001", codes)
            placeholder = next(
                finding for finding in report.findings if finding.code == "AUD006"
            )
            self.assertEqual(placeholder.severity, "error")

    def test_custom_secret_pattern(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files["AGENTS.md"] = "# Role\n\nTOKEN-12345\n"
            files[".agents/audit.yaml"] = (
                "apiVersion: dotagent/v1alpha1\n"
                "kind: AuditConfig\n"
                "secret_patterns: ['TOKEN-[0-9]+']\n"
            )
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            self.assertIn("AUD001", {finding.code for finding in report.findings})


class CacheTests(unittest.TestCase):
    def test_touch_without_content_change_reuses_build(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            compiled = compile_agent(project)
            out = Path(directory) / "build"
            write_compiled(compiled, out)
            write_lock(compiled)
            refresh_cache(project, project / ".agents")
            lock = read_lock(project / ".agents" / "dotagent.lock")
            agents = project / "AGENTS.md"
            future = time.time() + 100
            os.utime(agents, (future, future))
            self.assertIsNotNone(
                incremental_status(project, project / ".agents", lock, out / "agent.json")
            )
            agents.write_text("# Changed\n", encoding="utf-8")
            self.assertIsNone(
                incremental_status(project, project / ".agents", lock, out / "agent.json")
            )


class CertificateChainTests(unittest.TestCase):
    def test_chain_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(EXAMPLE)
            key, certificate, root = generate_certificate_chain(base / "keys")
            first = package_agent(
                compiled,
                base / "first.zip",
                sign_key=key,
                sign_cert=certificate,
            )
            self.assertTrue(
                verify_package(first, ca_cert=root, require_signature=True).ok
            )
            rotated_dir = base / "rotated"
            rotated_key, rotated_cert = rotate_signing_certificate(
                rotated_dir,
                root_key_path=base / "keys" / "root-key.pem",
                root_cert_path=root,
            )
            second = package_agent(
                compiled,
                base / "second.zip",
                sign_key=rotated_key,
                sign_cert=rotated_cert,
            )
            self.assertTrue(
                verify_package(second, ca_cert=root, require_signature=True).ok
            )
            self.assertTrue(
                verify_package(first, ca_cert=root, require_signature=True).ok
            )
            _, _, wrong_root = generate_certificate_chain(base / "wrong")
            self.assertFalse(verify_package(first, ca_cert=wrong_root).ok)


class McpDiscoveryTests(unittest.TestCase):
    def test_discover_and_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            server = base / "server.py"
            server.write_text(
                """\
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}}), flush=True)
""",
                encoding="utf-8",
            )
            files = minimal_project()
            files[".agents/mcp.json"] = json.dumps(
                {
                    "mcpServers": {
                        "fake": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [str(server)],
                        }
                    }
                }
            )
            project = write_project(base / "agent", files)
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            report = discover_agent(definition, timeout=5)
            self.assertEqual([tool.name for tool in report.discoveries[0].tools], ["echo"])
            report = import_tools(definition, report)
            self.assertTrue(report.files)
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertIsNotNone(definition.tool("fake-echo"))


class ExtraProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_agent(EXAMPLE)

    def test_dsh_projection(self):
        result = project_agent(self.compiled, "dsh")
        files = result.file_map()
        package = json.loads(files["dsh-preset/package.json"].decode())
        self.assertTrue(package["dsh"]["bundle"]["patch"])
        patch = yaml.safe_load(files["dsh-preset/cordis.patch.yml"].decode())
        self.assertEqual(
            patch[0]["insert"][0]["name"], "@deepseek-ai/dsh-agent-preset"
        )

    def test_openai_agent_scaffold_compiles(self):
        result = project_agent(self.compiled, "openai")
        source = result.file_map()["openai/agent.py"].decode()
        builtins.compile(source, "agent.py", "exec")
        self.assertIn("from agents import Agent", source)

    def test_openai_chat_payload(self):
        result = project_agent(self.compiled, "openai-chat")
        payload = json.loads(result.file_map()["openai-chat.json"].decode())
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertTrue(payload["tools"])

    def test_openai_chat_import(self):
        from dotagent.integrations import import_openai

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            result = project_agent(self.compiled, "openai-chat")
            payload = result.file_map()["openai-chat.json"]
            source = base / "openai-chat.json"
            source.write_bytes(payload)
            report = import_openai(source, out=base / "imported")
            validation, definition = validate_source(load_source(report.out))
            self.assertTrue(validation.ok, [d.format() for d in validation.diagnostics])
            self.assertIsNotNone(definition.tool("web-search"))

    def test_openai_agent_import(self):
        from dotagent.integrations import import_openai

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            result = project_agent(self.compiled, "openai")
            for path, data in result.file_map().items():
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            report = import_openai(base / "openai", out=base / "imported")
            validation, _definition = validate_source(load_source(report.out))
            self.assertTrue(validation.ok, [d.format() for d in validation.diagnostics])

    def test_dsh_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "preset.yml").write_text(
                "name: Review\ndescription: Reviews changes.\norder: 1\n",
                encoding="utf-8",
            )
            (base / "agent.cordis.yml").write_text(
                "- id: persona\n  name: '@deepseek-ai/dsh-persona'\n"
                "  config:\n    prefix: You review code.\n"
                "- id: tool-bash\n  name: '@deepseek-ai/dsh-tool-bash'\n",
                encoding="utf-8",
            )
            self.assertTrue(looks_like_dsh(base))
            report = import_dsh(base, out=base / "imported")
            result, definition = validate_source(load_source(report.out))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertEqual(definition.instructions.text, "You review code.")
            self.assertIsNotNone(definition.tool("bash"))


if __name__ == "__main__":
    unittest.main()
