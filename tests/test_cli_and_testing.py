"""Tests for the declarative test runner and the CLI."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from dotagent.cli import main
from dotagent.core.errors import TestRunError
from dotagent.testkit import run_tests
from dotagent.testkit.checks import evaluate, resolve_path
from dotagent.testkit.fixtures import (
    copy_project,
    minimal_project,
    write_project,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-agent"


def run_cli(*argv: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


class TestRunnerTests(unittest.TestCase):
    def test_example_passes(self):
        report = run_tests(EXAMPLE)
        self.assertTrue(report.ok, [case.message for case in report.failures])
        self.assertGreater(report.counts()["passed"], 0)

    def test_golden_update_then_compare(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            report = run_tests(project)
            self.assertIn("golden/agent", {case.id for case in report.cases})
            run_tests(project, update=True)
            golden = project / ".agents" / "tests" / "golden" / "agent.json"
            self.assertTrue(golden.is_file())
            report = run_tests(project)
            golden_case = next(case for case in report.cases if case.id == "golden/agent")
            self.assertEqual(golden_case.status, "passed")
            golden.write_text("{}", encoding="utf-8")
            report = run_tests(project)
            golden_case = next(case for case in report.cases if case.id == "golden/agent")
            self.assertEqual(golden_case.status, "failed")

    def test_custom_check_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            checks = project / ".agents" / "tests" / "checks.yaml"
            checks.write_text(
                checks.read_text(encoding="utf-8")
                + "  - id: missing-tool\n    path: tools.nope\n    op: exists\n",
                encoding="utf-8",
            )
            report = run_tests(project, suites=("custom",))
            self.assertFalse(report.ok)
            self.assertEqual(report.failures[0].id, "custom/missing-tool")

    def test_limit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            manifest = project / ".agents" / "agent.yaml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "knowledge_chars: 20000", "knowledge_chars: 10"
                ),
                encoding="utf-8",
            )
            report = run_tests(project, suites=("limits",))
            self.assertFalse(report.ok)
            self.assertEqual(report.failures[0].id, "limits/knowledge-chars")

    def test_unknown_suite(self):
        with self.assertRaises(TestRunError):
            run_tests(EXAMPLE, suites=("nope",))

    def test_resolve_path(self):
        data = {"a": {"b": [1, 2, {"c": "x"}]}}
        self.assertEqual(resolve_path(data, "a.b.2.c"), "x")
        self.assertIs(resolve_path(data, "a.b.9"), resolve_path(data, "nope"))

    def test_evaluate_ops(self):
        self.assertTrue(evaluate("contains", ["a", "b"], "a", "p")[0])
        self.assertTrue(evaluate("matches", "abc", "^a", "p")[0])
        self.assertFalse(evaluate("min_length", "abc", 5, "p")[0])
        self.assertFalse(evaluate("equals", 1, True, "p")[0])


class CliTests(unittest.TestCase):
    def test_validate_ok(self):
        code, out, _ = run_cli("validate", str(EXAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("no problems found", out)

    def test_validate_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/agent.yaml"] = files[".agents/agent.yaml"].replace(
                "  model: default", "  model: missing"
            )
            project = write_project(Path(directory), files)
            code, _, err = run_cli("validate", str(project))
            self.assertEqual(code, 1)
            self.assertIn("DTA301", err)

    def test_compile_and_check(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            out = Path(directory) / "build"
            code, _, _ = run_cli("compile", str(project), "-o", str(out))
            self.assertEqual(code, 0)
            self.assertTrue((out / "agent.json").is_file())
            code, _, _ = run_cli("compile", str(project), "-o", str(out), "--check")
            self.assertEqual(code, 0)
            (project / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")
            code, _, _ = run_cli("compile", str(project), "-o", str(out), "--check")
            self.assertEqual(code, 1)

    def test_compile_locked_and_incremental(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            code, _, _ = run_cli("compile", str(project))
            self.assertEqual(code, 0)
            code, _, _ = run_cli("lock", str(project))
            self.assertEqual(code, 0)
            code, out, _ = run_cli("compile", str(project), "--incremental")
            self.assertEqual(code, 0)
            self.assertIn("up to date", out)
            code, _, _ = run_cli("compile", str(project), "--locked")
            self.assertEqual(code, 0)
            (project / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")
            code, _, _ = run_cli("compile", str(project), "--locked")
            self.assertEqual(code, 1)
            code, out, _ = run_cli("compile", str(project), "--incremental")
            self.assertEqual(code, 0)
            self.assertNotIn("up to date", out)

    def test_project(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "claude"
            code, _, _ = run_cli(
                "project", str(EXAMPLE), "--target", "claude", "-o", str(out)
            )
            self.assertEqual(code, 0)
            self.assertTrue((out / "CLAUDE.md").is_file())
            self.assertTrue((out / ".claude" / "agents" / "source-checker.md").is_file())

    def test_project_list(self):
        code, out, _ = run_cli("project", "--list")
        self.assertEqual(code, 0)
        self.assertIn("claude", out)

    def test_package_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "agent.zip"
            code, _, _ = run_cli("package", str(EXAMPLE), "-o", str(package), "--verify")
            self.assertEqual(code, 0)
            code, out, _ = run_cli("verify", str(package))
            self.assertEqual(code, 0)
            self.assertIn("ok", out)

    def test_package_signing(self):
        with tempfile.TemporaryDirectory() as directory:
            keys = Path(directory) / "keys"
            code, _, _ = run_cli("keygen", "--out", str(keys))
            self.assertEqual(code, 0)
            package = Path(directory) / "signed.zip"
            code, _, _ = run_cli(
                "package",
                str(EXAMPLE),
                "-o",
                str(package),
                "--sign-key",
                str(keys / "private.pem"),
            )
            self.assertEqual(code, 0)
            code, out, _ = run_cli(
                "verify",
                str(package),
                "--public-key",
                str(keys / "public.pem"),
                "--require-signature",
            )
            self.assertEqual(code, 0)
            self.assertIn("signed by", out)

    def test_test_command_json(self):
        code, out, _ = run_cli("test", str(EXAMPLE), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertIn("cases", payload)

    def test_schema(self):
        code, out, _ = run_cli("schema", "agent")
        self.assertEqual(code, 0)
        self.assertIn("DotAgent agent manifest", out)
        code, out, _ = run_cli("schema")
        self.assertEqual(code, 0)
        self.assertIn("common.schema.json", out)

    def test_schema_check(self):
        code, out, err = run_cli("schema", "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("ok: agent.schema.json", out)

    def test_init(self):
        with tempfile.TemporaryDirectory() as directory:
            code, _, _ = run_cli("init", "demo", "--path", directory)
            self.assertEqual(code, 0)
            self.assertTrue((Path(directory) / "AGENTS.md").is_file())
            self.assertTrue((Path(directory) / ".agents" / "agent.yaml").is_file())
            code, _, _ = run_cli("init", "demo", "--path", directory)
            self.assertEqual(code, 1)
            code, _, _ = run_cli("validate", directory)
            self.assertEqual(code, 0)

    def test_init_default_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            resource = Path(directory) / "resource"
            code, out, _ = run_cli(
                "--resource", str(resource), "init", "resource-agent"
            )
            self.assertEqual(code, 0)
            self.assertTrue(
                (resource / "agent" / "resource-agent" / ".agents" / "agent.yaml").is_file()
            )
            code, out, _ = run_cli("--resource", str(resource), "list")
            self.assertEqual(code, 0)
            self.assertIn("resource-agent", out)

    def test_audit_and_visualize(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            code, out, _ = run_cli("audit", str(project), "--fail-on", "never")
            self.assertEqual(code, 0)
            self.assertIn("wrote", out)
            self.assertTrue((project / "reports" / "audit" / "audit.json").is_file())
            code, out, _ = run_cli("visualize", str(project))
            self.assertEqual(code, 0)
            self.assertTrue((project / "reports" / "visual" / "index.html").is_file())

    def test_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            run_cli("compile", str(project))
            self.assertTrue((project / "build").is_dir())
            code, _, _ = run_cli("reset", str(project), "--yes")
            self.assertEqual(code, 0)
            self.assertFalse((project / "build").exists())

    def test_skill_add(self):
        with tempfile.TemporaryDirectory() as directory:
            project = copy_project(EXAMPLE, directory)
            code, _, _ = run_cli(
                "skill", "add", "new-skill", "--agent", str(project)
            )
            self.assertEqual(code, 0)
            self.assertTrue(
                (project / ".agents" / "skills" / "new-skill" / "SKILL.md").is_file()
            )
            code, _, _ = run_cli("validate", str(project))
            self.assertEqual(code, 0)

    def test_import_claude(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "claude"
            (source / ".claude" / "agents").mkdir(parents=True)
            (source / "CLAUDE.md").write_text("# Demo\n\nBe careful.\n", encoding="utf-8")
            (source / ".claude" / "agents" / "reviewer.md").write_text(
                "---\nname: reviewer\ndescription: Reviews.\ntools: Read, Grep\n---\n\nReview.\n",
                encoding="utf-8",
            )
            out = Path(directory) / "imported"
            code, _, _ = run_cli(
                "import", str(source), "--from", "claude", "--out", str(out)
            )
            self.assertEqual(code, 0)
            code, _, _ = run_cli("validate", str(out))
            self.assertEqual(code, 0)
            self.assertTrue((out / ".agents" / "tools" / "read.yaml").is_file())

    def test_inspect(self):
        code, out, _ = run_cli("inspect", str(EXAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("research-agent", out)
        self.assertIn("fingerprint", out)


if __name__ == "__main__":
    unittest.main()
