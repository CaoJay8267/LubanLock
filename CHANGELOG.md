# Changelog

All notable changes to DotAgent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use
[Semantic Versioning](https://semver.org/).

## 0.8.0 — 2026-11-20

### Added

- DSH subagents are emitted as separate agent presets.
- Audit `when`/`unless` conditions and a Mermaid dependency graph.
- AES-GCM encryption for PKI cache entries.

## 0.7.0 — 2026-11-20

### Added

- Explicit `--timestamp-proxy` for RFC 3161 requests.
- Copilot path-level instructions projection/import.
- Best-effort `0700`/`0600` permissions for the PKI cache.

## 0.6.0 — 2026-11-20

### Added

- Real RFC 3161 token verification fixture and local CRL distribution-point test.
- PKI response cache with `--offline`, `--cache-max-age` and `--proxy`.
- Shared-library binary assets round-trip through package import.
- Audit rule `depends_on` graph with topological execution.
- GitHub Copilot projector and importer.

### Changed

- RFC 3161 verification now tries the token's embedded certificate chain.
- `dotagent import --from` accepts `copilot`.

## 0.5.0 — 2026-11-20

### Added

- Shared libraries now import skills and knowledge assets under a
  `libraries/<name>/...` namespace; lockfiles include external library files.
- MCP legacy SSE transport and WebSocket client (`dotagent[mcp]`).
- CRL distribution-point fetching, OCSP checking, and optional RFC 3161
  timestamps (`dotagent[timestamp]`).
- DSH projection maps skills, MCP manager servers, and subagent plugins.
- OpenAI projection uses `FunctionTool` for complex schemas and emits
  `handoffs` for direct child agents.
- Audit rules support categories and a JSON Schema-validated `parameters` map.

### Changed

- `dotagent verify` gained `--fetch-crl`, `--ocsp`, `--ocsp-url`,
  `--timestamp-cert` and `--require-timestamp`.

### Limitations

- DSH subagent instructions and workflows/tasks remain manual.
- OCSP/TSA responses are not cached; timestamp requests make signed packages
  non-reproducible.

## 0.4.0 — 2026-11-20

Optional capabilities release.

### Added

- Cross-agent shared libraries via `spec.uses` with SemVer constraints;
  tools/models are imported explicitly and duplicate names are rejected.
- MCP discovery cache (`--refresh`, `--max-age`) and SSE response parsing.
- CRL verification (`dotagent verify --crl`).
- DSH projection maps known tool names to DSH tool plugins.
- OpenAI projection generates `tools.py` handlers for simple JSON schemas.
- Pluggable audit rules through the `dotagent.audit_rules` entry point.
- Tests for shared libraries, MCP cache/HTTP, CRL revocation, DSH mapping,
  OpenAI handlers and custom audit rules.

### Changed

- Incremental compilation is disabled for agents that use shared libraries,
  because library files are outside the project stat scan.

### Limitations

- Shared libraries import tools/models only; skills/knowledge are not copied.
- Old SSE transport, WebSocket, OCSP and trusted timestamps are not implemented.
- DSH skill/MCP/subagent mapping and OpenAI complex-schema handlers remain manual.

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
