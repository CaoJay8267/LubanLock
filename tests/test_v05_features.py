import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import yaml

from dotagent.build.compiler import compile_agent
from dotagent.build.packaging import package_agent, verify_package
from dotagent.build.signing import generate_certificate_chain
from dotagent.project import project_agent
from dotagent.testkit.fixtures import minimal_project, tool_yaml, write_project


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
        from dotagent.audit import (
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
                "apiVersion: dotagent/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD901:\n    parameters:\n      limit: 3\n"
            )
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            self.assertIn("AUD901", [finding.code for finding in report.findings])
            self.assertEqual(report.categories()["custom"]["info"], 1)
            (project / ".agents" / "audit.yaml").write_text(
                "apiVersion: dotagent/v1alpha1\nkind: AuditConfig\n"
                "rules:\n  AUD901:\n    parameters:\n      limit: nope\n",
                encoding="utf-8",
            )
            report, _ = audit_agent(project)
            finding = next(item for item in report.findings if item.code == "AUD901")
            self.assertIn("invalid audit rule parameters", finding.message)


class CertFetchTests(unittest.TestCase):
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
            with patch("dotagent.build.signing._fetch_crls", return_value=[crl]):
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
                "dotagent.build.packaging.request_timestamp", return_value=b"token"
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
                "dotagent.build.signing._fetch_crls", return_value=[crl]
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
                "dotagent.build.packaging.request_timestamp", return_value=b"token"
            ), patch("dotagent.build.packaging.verify_timestamp") as verify:
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
