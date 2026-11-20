"""Tests for projection, packaging, signing and runtime adaptation."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from dotagent.build.compiler import compile_agent
from dotagent.build.packaging import package_agent, verify_package
from dotagent.build.signing import generate_keypair
from dotagent.core.errors import PackageError, ProjectionError, Severity
from dotagent.project import (
    ProjectedFile,
    ProjectionResult,
    get_projector,
    list_projectors,
    project_agent,
)
from dotagent.runtime import AgentRuntime, adapt, get_adapter, list_adapters
from dotagent.testkit.fixtures import minimal_project, write_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-agent"


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_agent(EXAMPLE)

    def test_registry_lists_builtins(self):
        names = {entry["name"] for entry in list_projectors()}
        self.assertTrue({"json", "markdown", "claude"} <= names)

    def test_json_projection(self):
        result = project_agent(self.compiled, "json")
        files = result.file_map()
        self.assertIn("agent.json", files)
        self.assertIn("assets/AGENTS.md", files)
        self.assertEqual(result.warnings, ())

    def test_markdown_projection(self):
        result = project_agent(self.compiled, "markdown")
        files = result.file_map()
        self.assertIn("README.md", files)
        self.assertIn("skills/web-research.md", files)
        self.assertIn("Research Agent", files["README.md"].decode())

    def test_claude_projection(self):
        result = project_agent(self.compiled, "claude")
        files = {path: data.decode("utf-8") for path, data in result.file_map().items()}
        self.assertIn("CLAUDE.md", files)
        agent_file = ".claude/agents/source-checker.md"
        self.assertIn(agent_file, files)
        front, _ = _split(files[agent_file])
        self.assertEqual(front["name"], "source-checker")
        self.assertIn("Verifies claims", front["description"])
        self.assertIn("tools", front)
        skill_file = ".claude/skills/web-research/SKILL.md"
        front, _ = _split(files[skill_file])
        self.assertEqual(front["name"], "web-research")
        self.assertIn("allowed-tools", front)
        self.assertIn(".claude/skills/web-research/checklist.md", files)
        self.assertTrue(all(w.severity is Severity.WARNING for w in result.warnings))

    def test_claude_omits_foreign_model(self):
        result = project_agent(self.compiled, "claude")
        content = result.file_map()[".claude/agents/source-checker.md"].decode()
        self.assertNotIn("model:", content.split("---")[1])

    def test_claude_writes_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/mcp.json"] = (
                '{"mcpServers": {"docs": {"type": "http", "url": "https://example.com/mcp"}}}'
            )
            project = write_project(Path(directory), files)
            compiled = compile_agent(project)
            result = project_agent(compiled, "claude")
            mcp = yaml.safe_load(result.file_map()[".mcp.json"].decode())
            self.assertIn("docs", mcp["mcpServers"])

    def test_unknown_target(self):
        with self.assertRaises(ProjectionError):
            get_projector("does-not-exist")

    def test_unsafe_projection_path_is_rejected(self):
        result = ProjectionResult("test", (ProjectedFile.text("../escape.md", "x"),))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ProjectionError):
            result.write(directory)

    def test_duplicate_projection_path(self):
        result = ProjectionResult(
            "test",
            (ProjectedFile.text("a.md", "1"), ProjectedFile.text("a.md", "2")),
        )
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ProjectionError):
            result.write(directory)


def _split(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise AssertionError("missing front matter")
    _, front, body = text.split("---", 2)
    return yaml.safe_load(front), body


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_agent(EXAMPLE)

    def test_zip_roundtrip_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            first = package_agent(self.compiled, Path(directory) / "a.zip")
            second = package_agent(self.compiled, Path(directory) / "b.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            verification = verify_package(first)
            self.assertTrue(verification.ok, verification.errors)
            self.assertEqual(verification.fingerprint, self.compiled.fingerprint)

    def test_directory_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "package"
            package_agent(self.compiled, target, fmt="dir")
            verification = verify_package(target)
            self.assertTrue(verification.ok, verification.errors)
            with self.assertRaises(PackageError):
                package_agent(self.compiled, target, fmt="dir")

    def test_unsupported_format(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(PackageError):
            package_agent(self.compiled, Path(directory) / "x", fmt="tar")

    def test_tampered_package_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            package = package_agent(self.compiled, Path(directory) / "agent.zip")
            with zipfile.ZipFile(package) as archive:
                entries = {
                    info.filename: archive.read(info)
                    for info in archive.infolist()
                    if not info.is_dir()
                }
            entries["agent.json"] = entries["agent.json"] + b" "
            tampered = Path(directory) / "tampered.zip"
            with zipfile.ZipFile(tampered, "w") as archive:
                for name, data in sorted(entries.items()):
                    archive.writestr(name, data)
            verification = verify_package(tampered)
            self.assertFalse(verification.ok)
            self.assertTrue(verification.errors)

    def test_unsafe_zip_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "evil.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../escape.txt", "boom")
            verification = verify_package(package)
            self.assertFalse(verification.ok)
            self.assertIn("unsafe", " ".join(verification.errors).lower())

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "empty.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("agent.json", "{}")
            verification = verify_package(package)
            self.assertFalse(verification.ok)
            self.assertIn("manifest", " ".join(verification.errors).lower())

    def test_signing_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            private, public = generate_keypair(Path(directory) / "keys")
            package = package_agent(
                self.compiled, Path(directory) / "signed.zip", sign_key=private
            )
            verification = verify_package(package, public_key=public, require_signature=True)
            self.assertTrue(verification.ok, verification.errors)
            self.assertIsNotNone(verification.signed_by)
            # a different key must fail
            _, other_public = generate_keypair(Path(directory) / "other")
            verification = verify_package(package, public_key=other_public)
            self.assertFalse(verification.ok)

    def test_unsigned_with_require_signature_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            package = package_agent(self.compiled, Path(directory) / "plain.zip")
            verification = verify_package(package, require_signature=True)
            self.assertFalse(verification.ok)
            self.assertIn("not signed", " ".join(verification.errors).lower())

    def test_tampered_signed_manifest_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            private, public = generate_keypair(Path(directory) / "keys")
            package = package_agent(
                self.compiled, Path(directory) / "signed.zip", sign_key=private
            )
            with zipfile.ZipFile(package) as archive:
                entries = {
                    info.filename: archive.read(info)
                    for info in archive.infolist()
                    if not info.is_dir()
                }
            manifest = entries["manifest.json"].replace(b'"description"', b'"description" ')
            entries["manifest.json"] = manifest
            tampered = Path(directory) / "tampered.zip"
            with zipfile.ZipFile(tampered, "w") as archive:
                for name, data in sorted(entries.items()):
                    archive.writestr(name, data)
            verification = verify_package(tampered, public_key=public, require_signature=True)
            self.assertFalse(verification.ok)


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = AgentRuntime.from_path(EXAMPLE)

    def test_system_prompt(self):
        prompt = self.runtime.system_prompt()
        self.assertIn("Research Agent", prompt)
        self.assertIn("web-research", prompt)
        self.assertIn("web_search", prompt)
        self.assertIn("## Instructions", prompt)

    def test_workflow_plan_layers(self):
        plan = self.runtime.workflow_plan("quick-research")
        self.assertEqual(plan.entry, "plan")
        self.assertEqual(plan.layers[0], ("plan",))
        self.assertEqual(len(plan.layers), 6)

    def test_lookups(self):
        self.assertEqual(self.runtime.skill("web-research").name, "web-research")
        self.assertIsNone(self.runtime.skill("nope"))
        with self.assertRaises(KeyError):
            self.runtime.workflow_plan("nope")

    def test_adapters(self):
        self.assertIn("dict", {entry["name"] for entry in list_adapters()})
        self.assertEqual(adapt(self.runtime.compiled), self.runtime.compiled.ir)
        with self.assertRaises(KeyError):
            get_adapter("nope")


if __name__ == "__main__":
    unittest.main()
