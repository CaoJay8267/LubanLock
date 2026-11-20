# Changelog

All notable changes to DotAgent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use
[Semantic Versioning](https://semver.org/).

## 0.3.1 — 2026-11-20

Hardening release; no schema changes.

### Added

- CI matrix: Python 3.11–3.14 on Ubuntu and Windows, plus a wheel build and
  installed-wheel smoke test.
- Coverage gate (80% for `dotagent/`) and mypy type checking.
- MCP HTTP JSON-RPC integration test; SSE response parsing; session header
  handling.
- `CHANGELOG.md`, development extras and release instructions.

### Changed

- The research-agent example now demonstrates `extends`, nested subagents
  (`uses`), an audit config and custom checks.
- Documentation now records v0.3 limitations and remaining work.

### Fixed

- Structural package verification no longer requires a trust anchor; certificate
  signatures are verified when `--ca-cert` is supplied.

## 0.3.0 — 2026-11-20

### Added

- `extends` inheritance for tools, models, subagents, workflows and tasks,
  with cycle and depth checks.
- Nested subagents through `agents` / `uses`, cycle and depth validation.
- MCP stdio/HTTP tool discovery (`dotagent mcp discover`) and schema import.
- Content-hash cache for incremental builds; touching a file without changing
  content no longer rebuilds.
- Ed25519 X.509 certificate chains and key rotation
  (`keygen --chain`, `keygen --rotate`, `--sign-cert`, `--ca-cert`).
- `dsh`, `openai` and `openai-chat` projections; DSH and OpenAI importers.
- Configurable audit rules in `.agents/audit.yaml`.

## 0.2.0 — 2026-11-20

### Added

- `AGENTS.md` + `.agents/` layout; `.agent/` and `subagents/` compatibility.
- Strict Agent Skills `SKILL.md` support.
- MCP declarations, Claude projection/import, package round-trip importer.
- Deterministic compilation, packaging, lockfile and declarative test suites.
- CLI, `scripts/main.py`, Chinese documentation and 99 tests.

### Changed

- Skills are directory-only; single-file skills are no longer accepted.

## 0.1.0 — 2026-11-20

- Initial framework skeleton (superseded by 0.2.0).
