"""Tests for schema validation, loading, validation and compilation."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from dotagent.build.compiler import check_compiled, compile_agent, write_compiled
from dotagent.core.utils import safe_load_yaml
from dotagent.spec.loader import load_source
from dotagent.spec.schema import (
    AGENT_SCHEMA,
    SCHEMA_DIR,
    check_all_schemas,
    get_schema_store,
    lint_json_schema,
    validate_document,
)
from dotagent.spec.schema.validator import validate_instance
from dotagent.spec.validator import validate_source
from dotagent.testkit.fixtures import (
    minimal_project,
    skill_front_matter,
    tool_yaml,
    write_project,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-agent"


def codes(diagnostics) -> list[str]:
    return [diagnostic.code for diagnostic in diagnostics]


def validate_inline(schema: dict, value: object):
    return validate_instance(
        value,
        schema,
        store=get_schema_store(),
        base_file=SCHEMA_DIR / "_inline.schema.json",
        document=schema,
    )


class SchemaTests(unittest.TestCase):
    def test_all_bundled_schemas_are_sane(self):
        for name, diagnostics in check_all_schemas().items():
            self.assertEqual(diagnostics, [], msg=name)

    def test_yaml_timestamps_stay_strings(self):
        self.assertEqual(safe_load_yaml("date: 2026-01-01"), {"date": "2026-01-01"})

    def test_yaml_aliases_are_rejected(self):
        with self.assertRaises(yaml.YAMLError):
            safe_load_yaml("a: &x [1, 2]\nb: *x\n")

    def test_minimal_agent_manifest_is_valid(self):
        manifest = {
            "apiVersion": "dotagent/v1alpha1",
            "kind": "Agent",
            "metadata": {"name": "agent"},
        }
        self.assertEqual(validate_document(manifest, AGENT_SCHEMA), [])

    def test_missing_name_reports_required(self):
        diagnostics = validate_document(
            {"apiVersion": "dotagent/v1alpha1", "kind": "Agent", "metadata": {}},
            AGENT_SCHEMA,
            path=".agents/agent.yaml",
        )
        self.assertIn("DTA201", codes(diagnostics))
        self.assertEqual({d.path for d in diagnostics}, {".agents/agent.yaml"})

    def test_unknown_property_is_rejected(self):
        diagnostics = validate_document(
            {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "agent"},
                "surprise": True,
            },
            AGENT_SCHEMA,
        )
        self.assertIn("DTA203", codes(diagnostics))

    def test_wrong_type_is_reported(self):
        diagnostics = validate_document(
            {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "agent", "description": 7},
            },
            AGENT_SCHEMA,
        )
        self.assertIn("DTA202", codes(diagnostics))

    def test_name_pattern_is_enforced(self):
        diagnostics = validate_document(
            {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "Bad_Name"},
            },
            AGENT_SCHEMA,
        )
        self.assertIn("DTA205", codes(diagnostics))

    def test_duplicate_tags_are_rejected(self):
        diagnostics = validate_document(
            {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "agent", "tags": ["a", "a"]},
            },
            AGENT_SCHEMA,
        )
        self.assertIn("DTA208", codes(diagnostics))

    def test_instructions_string_shorthand(self):
        for instructions in ("instructions.md", {"file": "instructions.md"}, {"text": "Hi"}):
            manifest = {
                "apiVersion": "dotagent/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "agent"},
                "spec": {"instructions": instructions},
            }
            self.assertEqual(validate_document(manifest, AGENT_SCHEMA), [])

    def test_lint_rejects_unsupported_keyword(self):
        diagnostics = lint_json_schema(
            {"type": "object", "unevaluatedProperties": False}, path="tool.yaml"
        )
        self.assertEqual(codes(diagnostics), ["DTA308"])

    def test_lint_rejects_bad_regex(self):
        diagnostics = lint_json_schema({"type": "string", "pattern": "("})
        self.assertEqual(codes(diagnostics), ["DTA308"])

    def test_pattern_properties(self):
        schema = {
            "type": "object",
            "patternProperties": {"^x-": {"type": "string"}},
            "additionalProperties": False,
        }
        self.assertEqual(validate_inline(schema, {"x-a": "ok"}), [])
        self.assertIn("DTA203", codes(validate_inline(schema, {"other": 1})))
        self.assertIn("DTA202", codes(validate_inline(schema, {"x-a": 1})))

    def test_contains_and_prefix_items(self):
        schema = {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {"type": "integer"},
            "contains": {"const": 3},
        }
        self.assertEqual(validate_inline(schema, ["a", 1, 3]), [])
        self.assertTrue(validate_inline(schema, ["a", "b"]))
        self.assertTrue(validate_inline(schema, ["a", 1]))

    def test_if_then_else(self):
        schema = {
            "type": "object",
            "if": {"properties": {"kind": {"const": "tool"}}, "required": ["kind"]},
            "then": {"required": ["schema"]},
            "else": {"required": ["description"]},
        }
        self.assertEqual(validate_inline(schema, {"kind": "tool", "schema": {}}), [])
        self.assertTrue(validate_inline(schema, {"kind": "tool"}))
        self.assertEqual(validate_inline(schema, {"kind": "doc", "description": "x"}), [])

    def test_dependent_required(self):
        schema = {"type": "object", "dependentRequired": {"a": ["b"]}}
        self.assertEqual(validate_inline(schema, {"a": 1, "b": 2}), [])
        self.assertIn("DTA201", codes(validate_inline(schema, {"a": 1})))


class LoaderTests(unittest.TestCase):
    def test_loads_the_example(self):
        source = load_source(EXAMPLE)
        self.assertEqual(len(source.entities["tool"]), 4)
        self.assertEqual(len(source.entities["skill"]), 2)
        self.assertEqual(len(source.entities["subagent"]), 2)
        self.assertEqual(source.instructions[0], "AGENTS.md")
        self.assertEqual(source.diagnostics, [])

    def test_resolves_project_root_with_dot_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            source = load_source(project)
            self.assertEqual(source.root, project.resolve())
            self.assertEqual(source.spec_dir, (project / ".agents").resolve())

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".agents").mkdir()
            source = load_source(Path(directory))
            self.assertIn("DTA101", codes(source.diagnostics))

    def test_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(Path(directory), {"AGENTS.md": "# x\n", ".agents/agent.yaml": "kind: [\n"})
            self.assertIn("DTA103", codes(load_source(Path(directory)).diagnostics))

    def test_malformed_front_matter(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/knowledge/bad.md"] = "---\nname: bad\n# no fence\n"
            write_project(Path(directory), files)
            self.assertIn("DTA104", codes(load_source(Path(directory)).diagnostics))

    def test_skill_directory_without_skill_md(self):
        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), minimal_project())
            (project / ".agents" / "skills" / "empty").mkdir(parents=True)
            self.assertIn("DTA110", codes(load_source(project).diagnostics))

    def test_single_file_skill_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/skills/legacy.md"] = skill_front_matter("legacy")
            project = write_project(Path(directory), files)
            self.assertIn("DTA110", codes(load_source(project).diagnostics))

    def test_legacy_dot_agent_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            files = {
                "AGENTS.md": "# Role\n",
                ".agent/agent.yaml": (
                    "apiVersion: dotagent/v1alpha1\n"
                    "kind: Agent\n"
                    "metadata:\n  name: old\n  description: Old.\n"
                ),
            }
            project = write_project(Path(directory), files)
            diagnostics = load_source(project).diagnostics
            self.assertIn("DTA910", codes(diagnostics))

    def test_mcp_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/mcp.json"] = (
                '{"mcpServers": {"docs": {"type": "http", "url": "https://example.com/mcp"}}}'
            )
            project = write_project(Path(directory), files)
            source = load_source(project)
            self.assertIsNotNone(source.mcp)
            self.assertEqual(source.mcp_rel, ".agents/mcp.json")


class ValidationTests(unittest.TestCase):
    def validate(self, files: dict[str, str]):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        project = write_project(Path(directory), files)
        result, definition = validate_source(load_source(project))
        return result, definition

    def test_minimal_agent_is_valid(self):
        result, definition = self.validate(minimal_project())
        self.assertTrue(result.ok, [d.format() for d in result.diagnostics])
        self.assertIsNotNone(definition)

    def test_unknown_model_reference(self):
        files = minimal_project()
        files[".agents/agent.yaml"] = files[".agents/agent.yaml"].replace(
            "  model: default", "  model: missing"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA301", codes(result.diagnostics))

    def test_skill_allowed_tool_must_exist(self):
        files = minimal_project()
        files[".agents/skills/demo/SKILL.md"] = (
            "---\nname: demo\ndescription: Demo skill.\n"
            "allowed-tools: missing\n---\n\nBody.\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA301", codes(result.diagnostics))

    def test_skill_name_must_match_directory(self):
        files = minimal_project()
        files[".agents/skills/actual/SKILL.md"] = (
            "---\nname: other\ndescription: Demo.\n---\n\nBody.\n"
        )
        result, definition = self.validate(files)
        self.assertIn("DTA908", codes(result.diagnostics))
        self.assertIsNotNone(definition)

    def test_duplicate_names_are_rejected(self):
        files = minimal_project()
        files[".agents/tools/echo.yaml"] = tool_yaml("echo")
        files[".agents/tools/echo-copy.yaml"] = tool_yaml("echo")
        result, _ = self.validate(files)
        self.assertIn("DTA108", codes(result.diagnostics))

    def test_unsafe_instruction_path(self):
        files = minimal_project()
        files[".agents/agent.yaml"] = files[".agents/agent.yaml"].replace(
            "spec:\n  model: default",
            "spec:\n  instructions: ../outside.md\n  model: default",
        )
        result, _ = self.validate(files)
        self.assertIn("DTA109", codes(result.diagnostics))

    def test_tool_input_schema_must_be_object(self):
        files = minimal_project()
        files[".agents/tools/echo.yaml"] = tool_yaml("echo", schema="    type: string\n")
        result, _ = self.validate(files)
        self.assertIn("DTA308", codes(result.diagnostics))

    def test_workflow_cycle(self):
        files = minimal_project()
        files[".agents/workflows/flow.yaml"] = (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Workflow\n"
            "metadata:\n  name: flow\n"
            "spec:\n"
            "  entry: a\n"
            "  nodes:\n"
            "    - {id: a, kind: human}\n"
            "    - {id: b, kind: human}\n"
            "  edges:\n"
            "    - {from: a, to: b}\n"
            "    - {from: b, to: a}\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA304", codes(result.diagnostics))

    def test_duplicate_node_ids(self):
        files = minimal_project()
        files[".agents/workflows/flow.yaml"] = (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Workflow\n"
            "metadata:\n  name: flow\n"
            "spec:\n"
            "  entry: a\n"
            "  nodes:\n"
            "    - {id: a, kind: human}\n"
            "    - {id: a, kind: human}\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA302", codes(result.diagnostics))

    def test_missing_workflow_entry(self):
        files = minimal_project()
        files[".agents/workflows/flow.yaml"] = (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Workflow\n"
            "metadata:\n  name: flow\n"
            "spec:\n"
            "  entry: nowhere\n"
            "  nodes:\n"
            "    - {id: a, kind: human}\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA305", codes(result.diagnostics))

    def test_unknown_task_reference(self):
        files = minimal_project()
        files[".agents/workflows/flow.yaml"] = (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Workflow\n"
            "metadata:\n  name: flow\n"
            "spec:\n"
            "  entry: a\n"
            "  nodes:\n"
            "    - {id: a, kind: task, ref: missing}\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA301", codes(result.diagnostics))

    def test_limit_warning(self):
        files = minimal_project()
        files[".agents/knowledge/note.md"] = "---\nname: note\n---\n\n" + "x" * 100
        files[".agents/agent.yaml"] = files[".agents/agent.yaml"].replace(
            "  model: default", "  model: default\n  limits:\n    knowledge_chars: 10"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA401", codes(result.diagnostics))
        self.assertTrue(result.ok)

    def test_task_example_is_validated_against_input_schema(self):
        files = minimal_project()
        files[".agents/tasks/run.yaml"] = (
            "apiVersion: dotagent/v1alpha1\n"
            "kind: Task\n"
            "metadata:\n  name: run\n  description: Run.\n"
            "spec:\n"
            "  prompt:\n    text: Do it.\n"
            "  input_schema:\n"
            "    type: object\n"
            "    required: [question]\n"
            "    properties:\n"
            "      question: {type: string, minLength: 5}\n"
            "  examples:\n"
            "    - input: {question: x}\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA205", codes(result.diagnostics))

    def test_subagent_unknown_tool(self):
        files = minimal_project()
        files[".agents/agents/checker.md"] = (
            "---\nname: checker\ndescription: Checks.\ntools: [missing]\n---\n\nBody.\n"
        )
        result, _ = self.validate(files)
        self.assertIn("DTA301", codes(result.diagnostics))

    def test_mcp_server_without_transport_warns(self):
        files = minimal_project()
        files[".agents/mcp.json"] = '{"mcpServers": {"broken": {}}}'
        result, _ = self.validate(files)
        self.assertIn("DTA912", codes(result.diagnostics))
        self.assertTrue(result.ok)


class CompilerTests(unittest.TestCase):
    def test_compiles_deterministically(self):
        first = compile_agent(EXAMPLE)
        second = compile_agent(EXAMPLE)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.to_json_text(), second.to_json_text())
        self.assertTrue(first.fingerprint.startswith("sha256:"))
        self.assertIn("AGENTS.md", [asset.path for asset in first.assets])
        self.assertEqual(len(first.assets), 8)

    def test_write_and_check(self):
        with tempfile.TemporaryDirectory() as directory:
            compiled = compile_agent(EXAMPLE)
            out = Path(directory) / "build"
            write_compiled(compiled, out)
            self.assertTrue((out / "agent.json").is_file())
            ok, message = check_compiled(compiled, out)
            self.assertTrue(ok, message)
            (out / "agent.json").write_text("{}", encoding="utf-8")
            ok, _ = check_compiled(compiled, out)
            self.assertFalse(ok)

    def test_strict_mode_rejects_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            files = minimal_project()
            files[".agents/agent.yaml"] = files[".agents/agent.yaml"].replace(
                "  description: A test agent.\n", ""
            )
            project = write_project(Path(directory), files)
            compile_agent(project)
            from dotagent.core.errors import ValidationFailed

            with self.assertRaises(ValidationFailed):
                compile_agent(project, strict=True)

    def test_invalid_agent_raises(self):
        from dotagent.core.errors import ValidationFailed

        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), {"AGENTS.md": "# x\n", ".agents/agent.yaml": "kind: Agent\n"})
            with self.assertRaises(ValidationFailed):
                compile_agent(project)


class ApiTests(unittest.TestCase):
    def test_load_and_validate(self):
        import dotagent

        agent = dotagent.load_agent(EXAMPLE)
        self.assertEqual(agent.metadata.name, "research-agent")
        result = dotagent.validate_agent(EXAMPLE)
        self.assertTrue(result.ok)

    def test_invalid_agent_raises(self):
        import dotagent

        with tempfile.TemporaryDirectory() as directory:
            project = write_project(Path(directory), {"AGENTS.md": "# x\n", ".agents/agent.yaml": "kind: Agent\n"})
            with self.assertRaises(dotagent.ValidationFailed):
                dotagent.load_agent(project)


if __name__ == "__main__":
    unittest.main()
