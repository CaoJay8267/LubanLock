import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import yaml

from lubanlock.build.compiler import compile_agent
from lubanlock.build.packaging import package_agent, verify_package
from lubanlock.build.signing import generate_certificate_chain
from lubanlock.project import project_agent
from lubanlock.spec.loader import load_source
from lubanlock.spec.validator import validate_source
from lubanlock.testkit.fixtures import minimal_project, tool_yaml, write_project


class DshMappingTests(unittest.TestCase):
    def test_skills_mcp_and_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/skills/cite/SKILL.md"] = (
                "---\nname: cite\ndescription: Cite.\n---\nUse citations.\n"
            )
            files[".agents/mcp.json"] = json.dumps(
                {
                    "mcpServers": {
                        "local": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "server"],
                            "env": {"TOKEN": "x"},
                        },
                        "remote": {
                            "type": "http",
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer x"},
                        },
                    }
                }
            )
            files[".agents/tools/web.yaml"] = tool_yaml("web_search")
            project = write_project(Path(directory), files)
            compiled = compile_agent(project)
            result = project_agent(compiled, "dsh")
            patch = yaml.safe_load(
                result.file_map()["dsh-preset/cordis.patch.yml"].decode()
            )
            plugins = patch[0]["insert"][0]["config"]["plugins"]
            names = [plugin["name"] for plugin in plugins]
            self.assertIn("@deepseek-ai/dsh-skill-filesystem", names)
            self.assertIn("@deepseek-ai/dsh-tool-skill", names)
            self.assertIn("@deepseek-ai/dsh-tool-web", names)
            manager = next(p for p in plugins if p["name"] == "@deepseek-ai/dsh-mcp-manager")
            servers = manager["config"]["servers"]
            self.assertEqual(servers["local"]["transport"], "stdio")
            self.assertEqual(servers["local"]["args"], ["-y", "server"])
            self.assertEqual(servers["remote"]["transport"], "streamable-http")
            self.assertEqual(
                servers["remote"]["headers"]["Authorization"], "Bearer x"
            )


class OpenAIMappingTests(unittest.TestCase):
    def test_complex_tool_and_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/tools/complex.yaml"] = tool_yaml(
                "complex",
                description="Complex.",
                schema=(
                    "    type: object\n"
                    "    properties:\n"
                    "      filters:\n"
                    "        type: object\n"
                    "        properties:\n"
                    "          q:\n"
                    "            type: string\n"
                ),
            )
            files[".agents/agents/helper.md"] = (
                "---\nname: helper\ndescription: Helper.\n---\nHelp with the task.\n"
            )
            project = write_project(Path(directory), files)
            compiled = compile_agent(project)
            result = project_agent(compiled, "openai")
            agent_source = result.file_map()["openai/agent.py"].decode()
            tools_source = result.file_map()["openai/tools.py"].decode()
            self.assertIn("handoffs=[", agent_source)
            self.assertIn("FunctionTool", tools_source)
            self.assertIn("params_json_schema=", tools_source)
            compile(agent_source, "agent.py", "exec")
            compile(tools_source, "tools.py", "exec")


class AuditParameterTests(unittest.TestCase):
    def test_categories_and_parameters(self):
        from lubanlock.audit import (
            AuditFinding,
            AuditRule,
            audit_agent,
            register_audit_rule,
        )

        class ParamRule(AuditRule):
            code = "AUD901"
            parameter_schema: ClassVar[dict] = {
                "type": "object",
                "required": ["limit"],
                "properties": {"limit": {"type": "integer"}},
            }

            def run(self, definition, config):
                limit = config.parameters(self.code).get("limit")
                return [
                    AuditFinding(
                        self.code,
                        "info",
                        f"limit={limit}",
                        category="custom",
                    )
                ]

        register_audit_rule(ParamRule())
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/audit.yaml"] = (
                "apiVersion: lubanlock/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD901:\n    parameters:\n      limit: 3\n"
            )
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            self.assertIn("AUD901", [finding.code for finding in report.findings])
            self.assertEqual(report.categories()["custom"]["info"], 1)
            (project / ".agents" / "audit.yaml").write_text(
                "apiVersion: lubanlock/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD901:\n    parameters:\n      limit: nope\n",
                encoding="utf-8",
            )
            report, _ = audit_agent(project)
            finding = next(item for item in report.findings if item.code == "AUD901")
            self.assertIn("invalid audit rule parameters", finding.message)

    def test_dependency_skips_rule(self):
        from lubanlock.audit import (
            AuditFinding,
            AuditRule,
            audit_agent,
            register_audit_rule,
        )

        class FirstRule(AuditRule):
            code = "AUD902"

            def run(self, definition, config):
                return [AuditFinding(self.code, "info", "first")]

        class SecondRule(AuditRule):
            code = "AUD903"

            def run(self, definition, config):
                return [AuditFinding(self.code, "info", "second")]

        class WhenRule(AuditRule):
            code = "AUD904"

            def run(self, definition, config):
                return [AuditFinding(self.code, "info", "when")]

        class UnlessRule(AuditRule):
            code = "AUD905"

            def run(self, definition, config):
                return [AuditFinding(self.code, "info", "unless")]

        register_audit_rule(FirstRule())
        register_audit_rule(SecondRule())
        register_audit_rule(WhenRule())
        register_audit_rule(UnlessRule())
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/audit.yaml"] = (
                "apiVersion: lubanlock/v1alpha1\nkind: AuditConfig\n"
                "rules:\n"
                "  AUD903:\n    depends_on: [AUD902]\n"
                "  AUD904:\n    when: [AUD902]\n"
                "  AUD905:\n    unless: [AUD902]\n"
            )
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            codes = [finding.code for finding in report.findings]
            self.assertIn("AUD902", codes)
            self.assertIn("AUD903", codes)
            self.assertIn("AUD904", codes)
            self.assertNotIn("AUD905", codes)
            self.assertEqual(report.dependencies["AUD903"], ("AUD902",))
            self.assertIn("graph TD", report.to_markdown())
            (project / ".agents" / "audit.yaml").write_text(
                "apiVersion: lubanlock/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD902:\n    enabled: false\n"
                "  AUD903:\n    depends_on: [AUD902]\n",
                encoding="utf-8",
            )
            report, _ = audit_agent(project)
            codes = [finding.code for finding in report.findings]
            self.assertNotIn("AUD903", codes)


class CopilotTests(unittest.TestCase):
    def test_projection_and_import(self):
        from lubanlock.integrations import import_copilot

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = write_project(base / "agent", minimal_project())
            compiled = compile_agent(project)
            result = project_agent(compiled, "copilot")
            text = result.file_map()[".github/copilot-instructions.md"].decode()
            self.assertIn("test-agent", text)
            source = base / "copilot"
            (source / ".github" / "instructions").mkdir(parents=True)
            (source / ".github" / "copilot-instructions.md").write_text(
                "# Instructions\n\nBe concise.\n", encoding="utf-8"
            )
            (source / ".github" / "instructions" / "python.instructions.md").write_text(
                '---\napplyTo: "**/*.py"\n---\n\nUse type hints.\n',
                encoding="utf-8",
            )
            report = import_copilot(source, out=base / "restored")
            result, definition = validate_source(load_source(report.out))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertIn("Be concise", definition.instructions.text)
            self.assertIn("python", [item.metadata.name for item in definition.knowledge])


class SharedAssetPackageTests(unittest.TestCase):
    def test_library_assets_survive_package_import(self):
        from lubanlock.integrations import import_package

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            library = minimal_project("asset-lib")
            library["AGENTS.md"] = "# Library\n"
            library[".agents/agent.yaml"] = library[".agents/agent.yaml"].replace(
                "  description: A test agent.\n",
                "  description: A test agent.\n  version: 0.2.0\n",
            )
            library.pop(".agents/models/default.yaml", None)
            library[".agents/skills/cite/SKILL.md"] = (
                "---\nname: cite\ndescription: Cite.\n---\nCite sources.\n"
            )
            library[".agents/skills/cite/blob.bin"] = "\x00\x01\x02binary"
            library[".agents/knowledge/lore.md"] = "---\nname: lore\n---\nLore.\n"
            write_project(base / "library", library)
            main = minimal_project("main")
            main[".agents/agent.yaml"] = main[".agents/agent.yaml"].replace(
                "spec:\n  model: default",
                "spec:\n  model: default\n  uses:\n"
                "    - path: ../library\n      version: '>=0.2.0'",
            )
            project = write_project(base / "main", main)
            compiled = compile_agent(project)
            package = package_agent(compiled, base / "agent.zip")
            self.assertTrue(verify_package(package).ok)
            report = import_package(package, out=base / "restored")
            result, definition = validate_source(load_source(report.out))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertIsNotNone(definition.skill("cite"))
            restored = Path(report.out) / ".agents" / "skills" / "cite" / "blob.bin"
            self.assertEqual(restored.read_bytes(), b"\x00\x01\x02binary")


class CertFetchTests(unittest.TestCase):
    def test_cache_encryption(self):
        from lubanlock.build.signing import _decrypt_cache, _encrypt_cache

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LUBANLOCK_CACHE_DIR": directory}, clear=False
        ):
            data = b"private CRL bytes"
            encrypted = _encrypt_cache(data)
            self.assertTrue(encrypted.startswith(b"DA1"))
            self.assertNotIn(data, encrypted)
            self.assertEqual(_decrypt_cache(encrypted), data)

    def test_timestamp_request_and_proxy(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        import rfc3161ng
        from pyasn1.codec.der import decoder, encoder

        from lubanlock.build.signing import request_timestamp, verify_timestamp

        token = (Path(__file__).parent / "fixtures" / "digicert.tsr").read_bytes()
        tst, _ = decoder.decode(token, asn1Spec=rfc3161ng.TimeStampToken())
        response = rfc3161ng.TimeStampResp()
        response["status"]["status"] = 0
        response["timeStampToken"] = tst
        payload = encoder.encode(response)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/tsa"
            token_response = request_timestamp(
                b"dotagent timestamp fixture", url, timeout=5
            )
            verify_timestamp(b"dotagent timestamp fixture", token_response)
            proxied = request_timestamp(
                b"dotagent timestamp fixture",
                url,
                timeout=5,
                proxy=url,
            )
            self.assertTrue(proxied)
        finally:
            server.shutdown()
            server.server_close()

    def test_real_crl_distribution_point(self):
        import threading
        from datetime import UTC, datetime, timedelta
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = self.server.crl
                self.send_response(200)
                self.send_header("Content-Type", "application/pkix-crl")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.crl = b""
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                compiled = compile_agent(
                    write_project(base / "agent", minimal_project())
                )
                url = f"http://127.0.0.1:{server.server_port}/revoked.crl"
                key, certificate, root = generate_certificate_chain(
                    base / "keys", crl_url=url
                )
                package = package_agent(
                    compiled,
                    base / "signed.zip",
                    sign_key=key,
                    sign_cert=certificate,
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
                server.crl = builder.sign(root_key, None).public_bytes(
                    serialization.Encoding.DER
                )
                self.assertTrue(
                    verify_package(
                        package,
                        ca_cert=root,
                        fetch_crl=True,
                        require_signature=True,
                    ).ok
                )
                self.assertTrue(
                    verify_package(
                        package,
                        ca_cert=root,
                        fetch_crl=True,
                        offline=True,
                        require_signature=True,
                    ).ok
                )
                server.crl = (
                    builder.add_revoked_certificate(
                        x509.RevokedCertificateBuilder()
                        .serial_number(leaf.serial_number)
                        .revocation_date(now)
                        .build()
                    )
                    .sign(root_key, None)
                    .public_bytes(serialization.Encoding.DER)
                )
                self.assertFalse(
                    verify_package(
                        package,
                        ca_cert=root,
                        fetch_crl=True,
                        cache_max_age=0,
                        require_signature=True,
                    ).ok
                )
        finally:
            server.shutdown()
            server.server_close()

    def test_real_rfc3161_token(self):
        from lubanlock.build.signing import verify_timestamp
        from lubanlock.core.errors import PackageError

        token = (Path(__file__).parent / "fixtures" / "digicert.tsr").read_bytes()
        verify_timestamp(b"dotagent timestamp fixture", token)
        with self.assertRaises(PackageError):
            verify_timestamp(b"different data", token)

    def test_crl_fetch_success(self):
        from datetime import UTC, datetime, timedelta

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(write_project(base / "agent", minimal_project()))
            key, certificate, root = generate_certificate_chain(base / "keys")
            package = package_agent(
                compiled, base / "signed.zip", sign_key=key, sign_cert=certificate
            )
            root_cert = x509.load_pem_x509_certificate(Path(root).read_bytes())
            root_key = serialization.load_pem_private_key(
                (base / "keys" / "root-key.pem").read_bytes(), password=None
            )
            now = datetime.now(UTC)
            crl = (
                x509.CertificateRevocationListBuilder()
                .issuer_name(root_cert.subject)
                .last_update(now)
                .next_update(now + timedelta(days=1))
                .sign(root_key, None)
            )
            with patch("lubanlock.build.signing._fetch_crls", return_value=[crl]):
                result = verify_package(
                    package, ca_cert=root, fetch_crl=True, require_signature=True
                )
            self.assertTrue(result.ok, result.errors)

    def test_crl_fetch_and_timestamp_wiring(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(
                write_project(base / "agent", minimal_project())
            )
            key, certificate, root = generate_certificate_chain(base / "keys")
            with patch(
                "lubanlock.build.packaging.request_timestamp", return_value=b"token"
            ):
                package = package_agent(
                    compiled,
                    base / "signed.zip",
                    sign_key=key,
                    sign_cert=certificate,
                    timestamp_url="https://tsa.invalid",
                )
            root_cert = x509.load_pem_x509_certificate(Path(root).read_bytes())
            root_key = serialization.load_pem_private_key(
                (base / "keys" / "root-key.pem").read_bytes(), password=None
            )
            from datetime import UTC, datetime, timedelta

            now = datetime.now(UTC)
            crl = (
                x509.CertificateRevocationListBuilder()
                .issuer_name(root_cert.subject)
                .last_update(now)
                .next_update(now + timedelta(days=1))
                .sign(root_key, None)
            )
            with patch(
                "lubanlock.build.signing._fetch_crls", return_value=[crl]
            ):
                result = verify_package(
                    package,
                    ca_cert=root,
                    fetch_crl=True,
                    require_timestamp=True,
                    require_signature=True,
                )
            self.assertFalse(result.ok)  # fake timestamp token is invalid

    def test_timestamp_wiring_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compiled = compile_agent(
                write_project(base / "agent", minimal_project())
            )
            key, certificate, _root = generate_certificate_chain(base / "keys")
            with patch(
                "lubanlock.build.packaging.request_timestamp", return_value=b"token"
            ), patch("lubanlock.build.packaging.verify_timestamp") as verify:
                package = package_agent(
                    compiled,
                    base / "signed.zip",
                    sign_key=key,
                    sign_cert=certificate,
                    timestamp_url="https://tsa.invalid",
                )
                self.assertTrue(package.is_file())
                verify.return_value = None


if __name__ == "__main__":
    unittest.main()
