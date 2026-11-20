"""Tests for audit, visualization, lockfiles, imports and MCP."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lubanlock.audit import audit_agent, visualize_agent
from lubanlock.build.compiler import compile_agent
from lubanlock.build.lockfile import check_lock, read_lock, reusable_output, write_lock
from lubanlock.build.packaging import package_agent
from lubanlock.integrations import import_claude, import_package
from lubanlock.spec.loader import load_source
from lubanlock.spec.validator import validate_source
from lubanlock.testkit.fixtures import minimal_project, write_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-agent"


class LockTests(unittest.TestCase):
    def test_lock_roundtrip_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            compiled = compile_agent(project)
            lock_file = write_lock(compiled)
            self.assertTrue(lock_file.is_file())
            lock = read_lock(lock_file)
            self.assertEqual(check_lock(compiled, lock), [])
            out = Path(directory) / "build"
            from lubanlock.build.compiler import write_compiled

            write_compiled(compiled, out)
            self.assertTrue(
                reusable_output(project, project / ".agents", lock, out / "agent.json")
            )
            (project / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")
            self.assertFalse(
                reusable_output(project, project / ".agents", lock, out / "agent.json")
            )


class AuditTests(unittest.TestCase):
    def test_audit_writes_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            report, files = audit_agent(project)
            self.assertTrue(report.ok)
            self.assertEqual(len(files), 2)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertIn("findings", payload)

    def test_secret_heuristic(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files["AGENTS.md"] = "# Role\n\nkey: sk-" + "a" * 30 + "\n"
            project = write_project(Path(directory), files)
            report, _ = audit_agent(project)
            self.assertIn("AUD001", [finding.code for finding in report.findings])


class VisualizationTests(unittest.TestCase):
    def test_visualize_writes_mermaid_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            files = minimal_project()
            files[".agents/workflows/main.yaml"] = (
                "apiVersion: lubanlock/v1alpha1\n"
                "kind: Workflow\n"
                "metadata:\n  name: main\n"
                "spec:\n"
                "  entry: a\n"
                "  nodes:\n"
                "    - {id: a, kind: human}\n"
            )
            project = write_project(Path(directory), files)
            result = visualize_agent(project)
            self.assertTrue(Path(result.out, "agent.mmd").is_file())
            self.assertTrue(Path(result.out, "workflows", "main.mmd").is_file())
            html = Path(result.out, "index.html").read_text(encoding="utf-8")
            self.assertIn("mermaid", html)


class ImportTests(unittest.TestCase):
    def test_package_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            package = package_agent(
                compile_agent(EXAMPLE), Path(directory) / "agent.zip"
            )
            out = Path(directory) / "imported"
            report = import_package(package, out=out)
            self.assertTrue(report.files)
            result, definition = validate_source(load_source(out))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertEqual(definition.metadata.name, "research-agent")

    def test_claude_import_with_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "claude"
            (source / ".claude" / "skills" / "demo").mkdir(parents=True)
            (source / "CLAUDE.md").write_text("# Demo\n\nBe careful.\n", encoding="utf-8")
            (source / ".mcp.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "docs": {"type": "http", "url": "https://example.com/mcp"}
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = import_claude(source, out=Path(directory) / "imported")
            result, definition = validate_source(load_source(report.out))
            self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
            self.assertEqual(len(definition.mcp), 1)


if __name__ == "__main__":
    unittest.main()
