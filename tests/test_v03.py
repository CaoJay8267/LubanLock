"""Tests for v0.3 features: reuse, nesting, cache, certificate chains, MCP,
DSH/OpenAI projections and configurable audits."""

from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    discover_server,
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


class SharedLibraryTests(unittest.TestCase):
    def _projects(self, directory: Path, constraint: str = ">=0.2.0,<0.3.0"):
        library = minimal_project("shared-lib")
        library["AGENTS.md"] = "# Shared\n"
        library[".agents/agent.yaml"] = library[".agents/agent.yaml"].replace(
            "  description: A test agent.\n",
            "  description: A test agent.\n  version: 0.2.0\n",
        )
        library[".agents/tools/shared.yaml"] = tool_yaml("shared", description="Shared.")
        library.pop(".agents/models/default.yaml", None)
        write_project(directory / "library", library)
        main = minimal_project("main")
        main[".agents/agent.yaml"] = main[".agents/agent.yaml"].replace(
            "spec:\n  model: default",
            "spec:\n  model: default\n  uses:\n"
            f"    - path: ../library\n      version: '{constraint}'",
        )
        return write_project(directory / "main", main)

    def test_import_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self._projects(Path(directory))
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertIsNotNone(definition.tool("shared"))
            self.assertEqual(definition.uses, (("../library", ">=0.2.0,<0.3.0"),))

    def test_imports_skills_knowledge_and_assets(self):
        from dotagent.build.lockfile import create_lock

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            library = minimal_project("shared-assets")
            library["AGENTS.md"] = "# Shared\n"
            library[".agents/agent.yaml"] = library[".agents/agent.yaml"].replace(
                "  description: A test agent.\n",
                "  description: A test agent.\n  version: 0.2.0\n",
            )
            library[".agents/skills/cite/SKILL.md"] = (
                "---\nname: cite\ndescription: Cite sources.\n---\nUse citations.\n"
            )
            library[".agents/skills/cite/reference.txt"] = "reference data\n"
            library[".agents/knowledge/lore.md"] = "---\nname: lore\n---\nShared lore.\n"
            library.pop(".agents/models/default.yaml", None)
            write_project(base / "library", library)
            main = minimal_project("main")
            main[".agents/agent.yaml"] = main[".agents/agent.yaml"].replace(
                "spec:\n  model: default",
                "spec:\n  model: default\n  uses:\n"
                "    - path: ../library\n      version: '>=0.2.0'",
            )
            project = write_project(base / "main", main)
            result, definition = validate_source(load_source(project))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertIsNotNone(definition.skill("cite"))
            self.assertEqual(definition.knowledge[0].metadata.name, "lore")
            compiled = compile_agent(project)
            self.assertIsNotNone(
                compiled.asset("libraries/library/skills/cite/reference.txt")
            )
            self.assertIsNotNone(
                compiled.asset("libraries/library/knowledge/lore.md")
            )
            lock = create_lock(compiled)
            self.assertIn(
                "libraries/library/skills/cite/reference.txt", lock["sources"]
            )
            from dotagent.build.lockfile import check_lock

            self.assertTrue(check_lock(compiled, lock) == [])
            (base / "library" / ".agents" / "skills" / "cite" / "reference.txt").write_text(
                "changed\n", encoding="utf-8"
            )
            self.assertTrue(any("hash mismatch" in problem for problem in check_lock(compiled, lock)))

    def test_version_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self._projects(Path(directory), ">=0.3.0")
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA331", _codes(result.diagnostics))

    def test_import_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self._projects(Path(directory))
            tools_dir = project / ".agents" / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / "shared.yaml").write_text(
                tool_yaml("shared", description="Local."), encoding="utf-8"
            )
            result, _ = validate_source(load_source(project))
            self.assertIn("DTA333", _codes(result.diagnostics))

    def test_incremental_disabled_with_libraries(self):
        from dotagent.build.cache import incremental_status

        with tempfile.TemporaryDirectory() as directory:
            project = self._projects(Path(directory))
            compiled = compile_agent(project)
            out = Path(directory) / "build"
            write_compiled(compiled, out)
            write_lock(compiled)
            lock = read_lock(project / ".agents" / "dotagent.lock")
            self.assertIsNone(
                incremental_status(project, project / ".agents", lock, out / "agent.json")
            )


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
    def test_custom_rule_and_disable(self):
        from dotagent.audit import (
            AuditFinding,
            AuditRule,
            audit_agent,
            register_audit_rule,
        )

        class DummyRule(AuditRule):
            code = "AUD900"
            description = "Always reports."

            def run(self, definition, config):
                return [
                    AuditFinding(
                        "AUD900",
                        "warning",
                        f"agent {definition.metadata.name} was inspected",
                    )
                ]

        register_audit_rule(DummyRule())
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/audit.yaml"] = (
                "apiVersion: dotagent/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD900:\n    enabled: false\n"
            )
            project = write_project(Path(directory) / "agent", files)
            report, _ = audit_agent(project)
            self.assertNotIn("AUD900", [finding.code for finding in report.findings])
            (project / ".agents" / "audit.yaml").write_text(
                "apiVersion: dotagent/v1alpha1\nkind: AuditConfig\n",
                encoding="utf-8",
            )
            report, _ = audit_agent(project)
            self.assertIn("AUD900", [finding.code for finding in report.findings])

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

    def test_crl_revocation(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(EXAMPLE)
            key, certificate, root = generate_certificate_chain(base / "keys")
            package = package_agent(
                compiled, base / "signed.zip", sign_key=key, sign_cert=certificate
            )
            root_cert = x509.load_pem_x509_certificate(Path(root).read_bytes())
            leaf = x509.load_pem_x509_certificate(Path(certificate).read_bytes())
            root_key = serialization.load_pem_private_key(
                (base / "keys" / "root-key.pem").read_bytes(), password=None
            )
            now = datetime.now(UTC)
            builder = (
                x509.CertificateRevocationListBuilder()
                .issuer_name(root_cert.subject)
                .last_update(now)
                .next_update(now + timedelta(days=1))
            )
            clean = builder.sign(root_key, None)
            clean_path = base / "clean.crl"
            clean_path.write_bytes(clean.public_bytes(serialization.Encoding.PEM))
            self.assertTrue(
                verify_package(
                    package, ca_cert=root, crl=clean_path, require_signature=True
                ).ok
            )
            revoked = (
                builder.add_revoked_certificate(
                    x509.RevokedCertificateBuilder()
                    .serial_number(leaf.serial_number)
                    .revocation_date(now)
                    .build()
                ).sign(root_key, None)
            )
            revoked_path = base / "revoked.crl"
            revoked_path.write_bytes(revoked.public_bytes(serialization.Encoding.PEM))
            self.assertFalse(
                verify_package(
                    package, ca_cert=root, crl=revoked_path, require_signature=True
                ).ok
            )

    def test_ocsp_revocation(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509 import ocsp

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(EXAMPLE)
            key, certificate, root = generate_certificate_chain(base / "keys")
            package = package_agent(
                compiled, base / "signed.zip", sign_key=key, sign_cert=certificate
            )
            root_cert = x509.load_pem_x509_certificate(Path(root).read_bytes())
            leaf = x509.load_pem_x509_certificate(Path(certificate).read_bytes())
            root_key = serialization.load_pem_private_key(
                (base / "keys" / "root-key.pem").read_bytes(), password=None
            )
            now = datetime.now(UTC)
            response = (
                ocsp.OCSPResponseBuilder()
                .add_response(
                    cert=leaf,
                    issuer=root_cert,
                    algorithm=hashes.SHA256(),
                    cert_status=ocsp.OCSPCertStatus.REVOKED,
                    this_update=now,
                    next_update=now + timedelta(days=1),
                    revocation_time=now,
                    revocation_reason=x509.ReasonFlags.key_compromise,
                )
                .responder_id(ocsp.OCSPResponderEncoding.HASH, root_cert)
                .sign(root_key, None)
            )
            payload = response.public_bytes(serialization.Encoding.DER)

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/ocsp-response")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, *args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/ocsp"
                result = verify_package(
                    package,
                    ca_cert=root,
                    ocsp=True,
                    ocsp_url=url,
                    require_signature=True,
                )
                self.assertFalse(result.ok)
                self.assertTrue(any("revoked" in error for error in result.errors))
            finally:
                server.shutdown()
                server.server_close()


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

    def test_discovery_cache(self):
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
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "cached", "description": "Cached", "inputSchema": {"type": "object", "properties": {}}}]}}), flush=True)
""",
                encoding="utf-8",
            )
            files = minimal_project()
            files[".agents/mcp.json"] = json.dumps(
                {
                    "mcpServers": {
                        "cache": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [str(server)],
                        }
                    }
                }
            )
            project = write_project(base / "agent", files)
            _, definition = validate_source(load_source(project))
            first = discover_agent(definition, timeout=5)
            self.assertEqual([tool.name for tool in first.discoveries[0].tools], ["cached"])
            self.assertTrue(
                (project / ".agents" / ".cache" / "mcp" / "cache.json").is_file()
            )
            files[".agents/mcp.json"] = json.dumps(
                {"mcpServers": {"cache": {"type": "stdio", "command": "missing-binary"}}}
            )
            write_project(project, files)
            _, definition = validate_source(load_source(project))
            second = discover_agent(definition, timeout=5)
            self.assertEqual([tool.name for tool in second.discoveries[0].tools], ["cached"])
            refreshed = discover_agent(definition, timeout=1, refresh=True)
            self.assertFalse(refreshed.discoveries)

    def test_http_discovery(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                message = json.loads(self.rfile.read(length))
                method = message.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "http", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": "Lookup",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"q": {"type": "string"}},
                                    "required": ["q"],
                                },
                            }
                        ]
                    }
                else:
                    result = {}
                payload = json.dumps(
                    {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "session-1")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/mcp"
            report = discover_server("http", {"type": "http", "url": url}, timeout=5)
            self.assertEqual([tool.name for tool in report.tools], ["lookup"])
        finally:
            server.shutdown()
            server.server_close()


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
        plugins = patch[0]["insert"][0]["config"]["plugins"]
        names = [plugin["name"] for plugin in plugins]
        self.assertIn("@deepseek-ai/dsh-persona", names)
        self.assertIn("@deepseek-ai/dsh-tool-web", names)

    def test_openai_agent_scaffold_compiles(self):
        result = project_agent(self.compiled, "openai")
        files = result.file_map()
        source = files["openai/agent.py"].decode()
        builtins.compile(source, "agent.py", "exec")
        self.assertIn("from agents import Agent", source)
        self.assertIn("tools.py", "\n".join(files))
        tools_source = files["openai/tools.py"].decode()
        builtins.compile(tools_source, "tools.py", "exec")
        self.assertIn("@function_tool", tools_source)

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
