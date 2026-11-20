"""The ``dotagent`` command line interface."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from dotagent._version import __version__
from dotagent.audit import audit_agent, visualize_agent
from dotagent.build.cache import incremental_status, refresh_cache
from dotagent.build.compiler import check_compiled, compile_agent, write_compiled
from dotagent.build.lockfile import check_lock, read_lock, write_lock
from dotagent.build.packaging import package_agent, verify_package
from dotagent.build.signing import (
    generate_certificate_chain,
    generate_keypair,
    rotate_signing_certificate,
)
from dotagent.core.errors import DotAgentError, LoadError, UsageError
from dotagent.core.paths import resolve_agent_layout, resolve_agent_target
from dotagent.integrations import (
    DshImportReport,
    ImportReport,
    OpenAIImportReport,
    import_claude,
    import_copilot,
    import_dsh,
    import_openai,
    import_package,
    looks_like_copilot,
    looks_like_dsh,
    looks_like_openai,
)
from dotagent.project import list_projectors, project_agent
from dotagent.spec.loader import load_source
from dotagent.spec.schema import (
    check_all_schemas,
    check_schema,
    load_schema,
    schema_names,
)
from dotagent.spec.validator import validate_source
from dotagent.templates import TEMPLATES, init_agent
from dotagent.testkit import SUITES, run_tests

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3

SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _print_diagnostics(diagnostics) -> None:
    for diagnostic in diagnostics:
        print(diagnostic.format(), file=sys.stderr)


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _target(value: str, resource: str) -> Path:
    try:
        return resolve_agent_target(value, resource_root=resource)
    except LoadError as exc:
        raise UsageError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotagent",
        description="Declarative, portable agent definitions built from AGENTS.md + .agents/.",
    )
    parser.add_argument(
        "--resource", default="resource", help="resource root (default: resource)"
    )
    parser.add_argument("--version", action="version", version=f"dotagent {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a new agent project")
    init.add_argument("name", nargs="?", default="my-agent", help="agent name")
    init.add_argument("--path", help="explicit project directory")
    init.add_argument("--template", choices=sorted(TEMPLATES), default="minimal")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    validate = subparsers.add_parser("validate", help="validate an agent project")
    validate.add_argument("path", nargs="?", default=".")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--strict", action="store_true")
    validate.set_defaults(func=cmd_validate)

    compile_cmd = subparsers.add_parser("compile", help="compile to agent.json + assets")
    compile_cmd.add_argument("path", nargs="?", default=".")
    compile_cmd.add_argument("-o", "--out")
    compile_cmd.add_argument("--check", action="store_true")
    compile_cmd.add_argument("--json", action="store_true")
    compile_cmd.add_argument("--strict", action="store_true")
    compile_cmd.add_argument("--locked", action="store_true")
    compile_cmd.add_argument("--incremental", action="store_true")
    compile_cmd.set_defaults(func=cmd_compile)

    lock = subparsers.add_parser("lock", help="write .agents/dotagent.lock")
    lock.add_argument("path", nargs="?", default=".")
    lock.add_argument("--json", action="store_true")
    lock.set_defaults(func=cmd_lock)

    project = subparsers.add_parser("project", help="project to another layout")
    project.add_argument("path", nargs="?", default=".")
    project.add_argument("-t", "--target")
    project.add_argument("-o", "--out")
    project.add_argument("--list", action="store_true")
    project.add_argument("--json", action="store_true")
    project.set_defaults(func=cmd_project)

    package = subparsers.add_parser("package", help="build a reproducible package")
    package.add_argument("path", nargs="?", default=".")
    package.add_argument("-o", "--out")
    package.add_argument("--format", choices=("zip", "dir"), default="zip")
    package.add_argument("--sign-key")
    package.add_argument("--sign-cert")
    package.add_argument("--sign-chain", action="append", default=[])
    package.add_argument("--timestamp-url")
    package.add_argument("--timestamp-timeout", type=float, default=30.0)
    package.add_argument("--timestamp-proxy")
    package.add_argument("--verify", action="store_true")
    package.add_argument("--json", action="store_true")
    package.set_defaults(func=cmd_package)

    verify = subparsers.add_parser("verify", help="verify a package")
    verify.add_argument("package")
    verify.add_argument("--public-key")
    verify.add_argument("--ca-cert", action="append", default=[])
    verify.add_argument("--crl", action="append", default=[], help="CRL (PEM/DER)")
    verify.add_argument("--fetch-crl", action="store_true", help="fetch CRL distribution points")
    verify.add_argument("--ocsp", action="store_true", help="check OCSP")
    verify.add_argument("--ocsp-url", help="override the certificate OCSP URL")
    verify.add_argument("--offline", action="store_true", help="use the PKI cache only")
    verify.add_argument("--cache-max-age", type=float, default=86400.0)
    verify.add_argument("--proxy", help="HTTP(S) proxy for CRL/OCSP")
    verify.add_argument("--timestamp-cert", help="TSA certificate for RFC 3161")
    verify.add_argument("--require-timestamp", action="store_true")
    verify.add_argument("--require-signature", action="store_true")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    test = subparsers.add_parser("test", help="run declarative test suites")
    test.add_argument("path", nargs="?", default=".")
    test.add_argument("--suite", action="append", choices=SUITES)
    test.add_argument("-k", "--filter")
    test.add_argument("--update", action="store_true")
    test.add_argument("--json", action="store_true")
    test.set_defaults(func=cmd_test)

    inspect = subparsers.add_parser("inspect", help="show a compiled summary")
    inspect.add_argument("path", nargs="?", default=".")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    schema = subparsers.add_parser("schema", help="print or check bundled JSON Schema")
    schema.add_argument("name", nargs="?")
    schema.add_argument("--check", action="store_true")
    schema.set_defaults(func=cmd_schema)

    import_cmd = subparsers.add_parser("import", help="import Claude Code or a package")
    import_cmd.add_argument("source")
    import_cmd.add_argument(
        "--from",
        dest="from_",
        choices=("auto", "claude", "package", "dsh", "openai", "copilot"),
        default="auto",
    )
    import_cmd.add_argument("--out")
    import_cmd.add_argument("--name")
    import_cmd.add_argument("--force", action="store_true")
    import_cmd.add_argument("--json", action="store_true")
    import_cmd.set_defaults(func=cmd_import)

    mcp = subparsers.add_parser("mcp", help="MCP discovery")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_discover = mcp_sub.add_parser(
        "discover", help="connect to servers and list/import tools"
    )
    mcp_discover.add_argument("path", nargs="?", default=".")
    mcp_discover.add_argument("--server", action="append", default=[])
    mcp_discover.add_argument("--write", action="store_true", help="write tool declarations")
    mcp_discover.add_argument("--timeout", type=float, default=10.0)
    mcp_discover.add_argument(
        "--refresh", action="store_true", help="ignore the discovery cache"
    )
    mcp_discover.add_argument(
        "--max-age", type=float, default=86400.0, help="cache TTL in seconds"
    )
    mcp_discover.add_argument("--json", action="store_true")
    mcp_discover.set_defaults(func=cmd_mcp_discover)

    audit = subparsers.add_parser("audit", help="audit an agent and write reports")
    audit.add_argument("path", nargs="?", default=".")
    audit.add_argument("--out")
    audit.add_argument(
        "--fail-on", choices=("error", "warning", "never"), default="error"
    )
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)

    visualize = subparsers.add_parser("visualize", help="write Mermaid/HTML diagrams")
    visualize.add_argument("path", nargs="?", default=".")
    visualize.add_argument("--out")
    visualize.add_argument("--json", action="store_true")
    visualize.set_defaults(func=cmd_visualize)

    keygen = subparsers.add_parser("keygen", help="create an Ed25519 signing keypair")
    keygen.add_argument("--out", default="keys")
    keygen.add_argument(
        "--chain", action="store_true", help="generate a root CA and signing certificate"
    )
    keygen.add_argument(
        "--rotate", action="store_true", help="issue a new leaf under an existing root"
    )
    keygen.add_argument("--root-key", help="root CA private key for rotation")
    keygen.add_argument("--root-cert", help="root CA certificate for rotation")
    keygen.add_argument("--json", action="store_true")
    keygen.set_defaults(func=cmd_keygen)

    skill = subparsers.add_parser("skill", help="manage skills")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_add = skill_sub.add_parser("add", help="add an Agent Skills directory")
    skill_add.add_argument("name")
    skill_add.add_argument("--agent", default=".")
    skill_add.add_argument("--description", default="Describe when to use this skill.")
    skill_add.add_argument("--force", action="store_true")
    skill_add.add_argument("--json", action="store_true")
    skill_add.set_defaults(func=cmd_skill_add)

    reset = subparsers.add_parser("reset", help="remove generated artifacts")
    reset.add_argument("path", nargs="?", default=".")
    reset.add_argument("--build", action="store_true", help="recompile afterwards")
    reset.add_argument("--yes", action="store_true", help="confirm deletion")
    reset.add_argument("--json", action="store_true")
    reset.set_defaults(func=cmd_reset)

    deploy = subparsers.add_parser("deploy", help="package and project an agent")
    deploy.add_argument("path", nargs="?", default=".")
    deploy.add_argument("--target", action="append", default=None)
    deploy.add_argument("--sign-key")
    deploy.add_argument("--json", action="store_true")
    deploy.set_defaults(func=cmd_deploy)

    agents = subparsers.add_parser("list", help="list agents under resource/agent")
    agents.add_argument("--json", action="store_true")
    agents.set_defaults(func=cmd_list)

    return parser


def cmd_init(args) -> int:
    root = init_agent(
        args.name,
        path=args.path,
        template=args.template,
        force=args.force,
        resource_root=args.resource,
    )
    print(f"created agent project: {root}")
    print(f"  {root / 'AGENTS.md'}")
    print(f"  {root / '.agents'}")
    return EXIT_OK


def cmd_validate(args) -> int:
    target = _target(args.path, args.resource)
    source = load_source(target)
    result, definition = validate_source(source)
    if args.json:
        _dump(result.to_dict())
    else:
        name = definition.metadata.name if definition else "(unparsed)"
        print(f"{name}  {source.root}")
        if definition is not None:
            counts = definition.entity_counts()
            summary = ", ".join(
                f"{count} {kind}" for kind, count in counts.items() if count
            )
            print(f"entities: {summary or 'none'}")
        if not result.diagnostics:
            print("no problems found")
        else:
            _print_diagnostics(result.sorted())
    if not result.ok:
        return EXIT_FAILURE
    if args.strict and result.warnings:
        return EXIT_FAILURE
    return EXIT_OK


def cmd_compile(args) -> int:
    target = _target(args.path, args.resource)
    layout = resolve_agent_layout(target)
    out = Path(args.out) if args.out else layout.project_root / "build"
    lock_file = layout.spec_dir / "dotagent.lock"

    if args.incremental and not args.check and not args.locked:
        lock = read_lock(lock_file)
        lock = incremental_status(
            layout.project_root, layout.spec_dir, lock, out / "agent.json"
        )
        if lock is not None:
            if args.json:
                _dump(
                    {
                        "ok": True,
                        "incremental": True,
                        "out": str(out),
                        "fingerprint": lock.get("fingerprint"),
                    }
                )
            else:
                print(f"up to date (incremental): {out}")
            return EXIT_OK

    compiled = compile_agent(target, strict=args.strict)
    if args.locked:
        lock = read_lock(lock_file)
        if lock is None:
            print(
                f"error: {lock_file} is missing; run 'dotagent lock' first",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        problems = check_lock(compiled, lock)
        if problems:
            for problem in problems:
                print(f"error: {problem}", file=sys.stderr)
            return EXIT_FAILURE
    if args.check:
        ok, message = check_compiled(compiled, out)
        if args.json:
            _dump({"ok": ok, "message": message, "out": str(out)})
        else:
            print(f"{'up to date' if ok else 'stale'}: {message}")
        return EXIT_OK if ok else EXIT_FAILURE
    write_compiled(compiled, out)
    refresh_cache(layout.project_root, layout.spec_dir)
    if args.json:
        _dump(
            {
                "ok": True,
                "agent": compiled.definition.metadata.name,
                "fingerprint": compiled.fingerprint,
                "out": str(out),
                "assets": len(compiled.assets),
                "warnings": [warning.to_dict() for warning in compiled.warnings],
            }
        )
    else:
        print(f"compiled {compiled.definition.metadata.name} -> {out}")
        print(f"fingerprint: {compiled.fingerprint}")
        print(f"assets: {len(compiled.assets)}")
        _print_diagnostics(compiled.warnings)
    return EXIT_OK


def cmd_lock(args) -> int:
    target = _target(args.path, args.resource)
    compiled = compile_agent(target)
    path = write_lock(compiled)
    refresh_cache(compiled.definition.root, compiled.definition.spec_dir)
    if args.json:
        _dump({"ok": True, "path": str(path), "fingerprint": compiled.fingerprint})
    else:
        print(f"wrote {path}")
        print(f"fingerprint: {compiled.fingerprint}")
    return EXIT_OK


def cmd_project(args) -> int:
    if args.list:
        for projector in list_projectors():
            print(f"{projector['name']:12} {projector['description']}")
        return EXIT_OK
    if not args.target:
        raise UsageError("--target is required; use --list to see available targets")
    target = _target(args.path, args.resource)
    compiled = compile_agent(target)
    result = project_agent(compiled, args.target)
    out = (
        Path(args.out)
        if args.out
        else compiled.definition.root / "dist" / args.target
    )
    written = result.write(out)
    _print_diagnostics(result.warnings)
    if args.json:
        _dump(
            {
                "target": args.target,
                "out": str(out),
                "files": [path.relative_to(out).as_posix() for path in written],
                "warnings": [warning.to_dict() for warning in result.warnings],
            }
        )
    else:
        print(f"projected {args.target} -> {out} ({len(written)} file(s))")
    return EXIT_OK


def cmd_package(args) -> int:
    target = _target(args.path, args.resource)
    compiled = compile_agent(target)
    root = compiled.definition.root
    name = compiled.definition.metadata.name
    version = compiled.definition.metadata.version
    suffix = "zip" if args.format == "zip" else ""
    default = root / "dist" / (
        f"{name}-{version}.{suffix}" if suffix else f"{name}-{version}"
    )
    out = Path(args.out) if args.out else default
    package = package_agent(
        compiled,
        out,
        fmt=args.format,
        sign_key=args.sign_key,
        sign_cert=args.sign_cert,
        sign_chain=tuple(args.sign_chain or ()),
        timestamp_url=args.timestamp_url,
        timestamp_timeout=args.timestamp_timeout,
        timestamp_proxy=args.timestamp_proxy,
    )
    verification = verify_package(package) if args.verify else None
    if args.json:
        payload = {
            "ok": True,
            "path": str(package),
            "format": args.format,
            "fingerprint": compiled.fingerprint,
        }
        if verification is not None:
            payload["verification"] = verification.to_dict()
        _dump(payload)
    else:
        print(f"packaged {name} {version} -> {package}")
        print(f"fingerprint: {compiled.fingerprint}")
        if verification is not None:
            print("verification: " + ("ok" if verification.ok else "failed"))
            for error in verification.errors:
                print(f"  {error}", file=sys.stderr)
    if verification is not None and not verification.ok:
        return EXIT_FAILURE
    return EXIT_OK


def cmd_verify(args) -> int:
    result = verify_package(
        args.package,
        public_key=args.public_key,
        ca_cert=tuple(args.ca_cert or ()),
        crl=tuple(args.crl or ()),
        fetch_crl=args.fetch_crl,
        ocsp=args.ocsp,
        ocsp_url=args.ocsp_url,
        offline=args.offline,
        cache_max_age=args.cache_max_age,
        proxy=args.proxy,
        timestamp_cert=args.timestamp_cert,
        require_timestamp=args.require_timestamp,
        require_signature=args.require_signature,
    )
    if args.json:
        _dump(result.to_dict())
    else:
        print(("ok" if result.ok else "failed") + f": {result.path}")
        if result.agent:
            print(f"agent: {result.agent.get('name')} {result.agent.get('version')}")
        if result.fingerprint:
            print(f"fingerprint: {result.fingerprint}")
        if result.signature:
            print(f"signed by: {result.signed_by} ({result.signature.get('alg')})")
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_test(args) -> int:
    target = _target(args.path, args.resource)
    report = run_tests(
        target,
        update=args.update,
        suites=tuple(args.suite) if args.suite else None,
        filter_text=args.filter,
    )
    if args.json:
        _dump(report.to_dict())
    else:
        for case in report.cases:
            print(f"{case.status.upper():7} {case.id:32} {case.title}")
            if case.status == "failed" and case.message:
                print(f"        {case.message}")
        counts = report.counts()
        print(
            f"\nSummary: {counts.get('passed', 0)} passed, "
            f"{counts.get('failed', 0)} failed, {counts.get('skipped', 0)} skipped"
        )
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_inspect(args) -> int:
    target = _target(args.path, args.resource)
    compiled = compile_agent(target)
    if args.json:
        print(compiled.to_json_text(), end="")
        return EXIT_OK
    agent = compiled.definition
    print(f"{agent.metadata.name} {agent.metadata.version}")
    print(f"root: {agent.root}")
    print(f"fingerprint: {compiled.fingerprint}")
    if agent.metadata.description:
        print(f"description: {agent.metadata.description}")
    for label, items in (
        ("knowledge", agent.knowledge),
        ("skills", agent.skills),
        ("agents", agent.subagents),
        ("tools", agent.tools),
        ("models", agent.models),
        ("workflows", agent.workflows),
        ("tasks", agent.tasks),
    ):
        names = ", ".join(
            item.name if hasattr(item, "name") else item.metadata.name for item in items
        )
        print(f"{label} ({len(items)}): {names}")
    if agent.mcp:
        print(f"mcp ({len(agent.mcp)}): {', '.join(name for name, _ in agent.mcp)}")
    if compiled.warnings:
        print(f"warnings: {len(compiled.warnings)}")
        _print_diagnostics(compiled.warnings)
    return EXIT_OK


def cmd_schema(args) -> int:
    if args.check:
        return _schema_check(args.name)
    names = schema_names()
    if not args.name:
        for name in names:
            print(name)
        return EXIT_OK
    candidate = args.name
    if not candidate.endswith(".json"):
        candidate = candidate.removesuffix(".schema") + ".schema.json"
    if candidate not in names:
        raise UsageError(f"unknown schema {args.name!r}; available: {', '.join(names)}")
    _dump(load_schema(candidate))
    return EXIT_OK


def _schema_check(name: str | None) -> int:
    if name:
        candidate = name if name.endswith(".json") else name.removesuffix(".schema") + ".schema.json"
        if candidate not in schema_names():
            raise UsageError(f"unknown schema {name!r}")
        results = {candidate: check_schema(candidate)}
    else:
        results = check_all_schemas()
    failed = False
    for schema_name, diagnostics in results.items():
        if diagnostics:
            failed = True
            print(f"failed: {schema_name}", file=sys.stderr)
            _print_diagnostics(diagnostics)
        else:
            print(f"ok: {schema_name}")
    return EXIT_FAILURE if failed else EXIT_OK


def cmd_import(args) -> int:
    source = Path(args.source)
    mode = args.from_
    if mode == "auto":
        if source.is_file():
            if looks_like_copilot(source):
                mode = "copilot"
            elif looks_like_openai(source):
                mode = "openai"
            elif source.suffix.lower() == ".zip":
                mode = "package"
            else:
                raise UsageError(
                    f"cannot detect the import format of {source}; use --from "
                    "claude|package|dsh|openai|copilot"
                )
        elif (source / "agent.json").is_file():
            mode = "package"
        elif (source / ".github" / "copilot-instructions.md").is_file():
            mode = "copilot"
        elif (source / ".claude").exists() or (source / "CLAUDE.md").is_file():
            mode = "claude"
        elif looks_like_dsh(source):
            mode = "dsh"
        elif looks_like_openai(source):
            mode = "openai"
        else:
            raise UsageError(
                f"cannot detect the import format of {source}; use --from "
                "claude|package|dsh|openai|copilot"
            )
    report: ImportReport | DshImportReport | OpenAIImportReport
    if mode == "claude":
        report = import_claude(
            source,
            out=args.out,
            name=args.name,
            force=args.force,
            resource_root=args.resource,
        )
    elif mode == "copilot":
        report = import_copilot(
            source,
            out=args.out,
            name=args.name,
            force=args.force,
            resource_root=args.resource,
        )
    elif mode == "dsh":
        report = import_dsh(
            source,
            out=args.out,
            name=args.name,
            force=args.force,
            resource_root=args.resource,
        )
    elif mode == "openai":
        report = import_openai(
            source,
            out=args.out,
            name=args.name,
            force=args.force,
            resource_root=args.resource,
        )
    else:
        report = import_package(
            source, out=args.out, force=args.force, resource_root=args.resource
        )
    if args.json:
        _dump(report.to_dict())
    else:
        print(f"imported {report.source} -> {report.out}")
        print(f"agent: {report.name}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return EXIT_OK


def cmd_mcp_discover(args) -> int:
    from dotagent import load_agent
    from dotagent.integrations import discover_agent, import_tools

    target = _target(args.path, args.resource)
    definition = load_agent(target)
    report = discover_agent(
        definition,
        servers=tuple(args.server or ()),
        timeout=args.timeout,
        refresh=args.refresh,
        max_age=args.max_age,
    )
    if args.write:
        report = import_tools(definition, report)
    if args.json:
        _dump(report.to_dict())
    else:
        for discovery in report.discoveries:
            print(f"{discovery.server} ({discovery.transport})")
            for tool in discovery.tools:
                print(f"  - {tool.name}: {tool.description}")
            for warning in discovery.warnings:
                print(f"    warning: {warning}", file=sys.stderr)
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for path in report.files:
            print(f"wrote {path}")
    return EXIT_OK


def cmd_audit(args) -> int:
    target = _target(args.path, args.resource)
    report, files = audit_agent(target, out=args.out)
    if args.json:
        _dump(report.to_dict())
    else:
        counts = report.counts()
        print(
            f"audit {report.agent}: {counts.get('error', 0)} error, "
            f"{counts.get('warning', 0)} warning, {counts.get('info', 0)} info"
        )
        for finding in report.findings:
            print(f"  [{finding.severity}] {finding.code} {finding.message}")
        for path in files:
            print(f"wrote {path}")
    if args.fail_on == "never":
        return EXIT_OK
    if args.fail_on == "warning" and any(
        finding.severity in {"warning", "error"} for finding in report.findings
    ):
        return EXIT_FAILURE
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_visualize(args) -> int:
    target = _target(args.path, args.resource)
    result = visualize_agent(target, out=args.out)
    if args.json:
        _dump(
            {
                "agent": result.agent,
                "out": result.out,
                "files": list(result.files),
                "diagrams": len(result.diagrams),
            }
        )
    else:
        print(f"visualized {result.agent} -> {result.out}")
        for path in result.files:
            print(f"  {path}")
    return EXIT_OK


def cmd_keygen(args) -> int:
    if args.rotate:
        if not args.root_key or not args.root_cert:
            raise UsageError("--rotate requires --root-key and --root-cert")
        private, certificate = rotate_signing_certificate(
            args.out, root_key_path=args.root_key, root_cert_path=args.root_cert
        )
        if args.json:
            _dump({"private": str(private), "certificate": str(certificate)})
        else:
            print(f"signing key: {private}")
            print(f"certificate: {certificate}")
        return EXIT_OK
    if args.chain:
        private, certificate, root = generate_certificate_chain(args.out)
        if args.json:
            _dump(
                {
                    "private": str(private),
                    "certificate": str(certificate),
                    "rootCa": str(root),
                }
            )
        else:
            print(f"signing key: {private}")
            print(f"certificate: {certificate}")
            print(f"root CA:     {root}")
        return EXIT_OK
    private, public = generate_keypair(args.out)
    if args.json:
        _dump({"private": str(private), "public": str(public)})
    else:
        print(f"private key: {private}")
        print(f"public key:  {public}")
    return EXIT_OK


def cmd_skill_add(args) -> int:
    if not SKILL_NAME.match(args.name):
        raise UsageError(
            "skill names must be lowercase alphanumeric with single hyphens "
            "(Agent Skills constraint)"
        )
    target = _target(args.agent, args.resource)
    layout = resolve_agent_layout(target)
    directory = layout.spec_dir / "skills" / args.name
    skill_file = directory / "SKILL.md"
    if skill_file.exists() and not args.force:
        raise UsageError(f"skill already exists: {skill_file}; pass --force")
    front = {"name": args.name, "description": args.description}
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        f"---\n{header}\n---\n\n# {args.name}\n\nDescribe the procedure here.\n",
        encoding="utf-8",
    )
    if args.json:
        _dump({"ok": True, "path": str(skill_file)})
    else:
        print(f"created {skill_file}")
    return EXIT_OK


def cmd_reset(args) -> int:
    target = _target(args.path, args.resource)
    layout = resolve_agent_layout(target)
    if not args.yes:
        raise UsageError(
            "reset removes build/, dist/ and reports/; pass --yes to confirm"
        )
    removed: list[str] = []
    for name in ("build", "dist", "reports"):
        directory = layout.project_root / name
        if directory.is_dir():
            shutil.rmtree(directory)
            removed.append(str(directory))
    lock = layout.spec_dir / "dotagent.lock"
    if lock.is_file():
        lock.unlink()
        removed.append(str(lock))
    rebuilt: str | None = None
    if args.build:
        compiled = compile_agent(layout.project_root)
        out = layout.project_root / "build"
        write_compiled(compiled, out)
        rebuilt = str(out)
    if args.json:
        _dump({"ok": True, "removed": removed, "rebuilt": rebuilt})
    else:
        for path in removed:
            print(f"removed {path}")
        if rebuilt:
            print(f"rebuilt {rebuilt}")
    return EXIT_OK


def cmd_deploy(args) -> int:
    target = _target(args.path, args.resource)
    compiled = compile_agent(target)
    root = compiled.definition.root
    name = compiled.definition.metadata.name
    version = compiled.definition.metadata.version
    package = package_agent(
        compiled,
        root / "dist" / f"{name}-{version}.zip",
        sign_key=args.sign_key,
    )
    verification = verify_package(package)
    if not verification.ok:
        for error in verification.errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    targets = args.target or ["claude", "markdown"]
    outputs = [str(package)]
    for target_name in targets:
        result = project_agent(compiled, target_name)
        out = root / "dist" / target_name
        result.write(out)
        outputs.append(str(out))
    if args.json:
        _dump({"ok": True, "package": str(package), "outputs": outputs})
    else:
        print(f"deployed {name} {version}")
        for path in outputs:
            print(f"  {path}")
    return EXIT_OK


def cmd_list(args) -> int:
    resource = Path(args.resource) / "agent"
    agents = []
    if resource.is_dir():
        agents = sorted(
            path.name
            for path in resource.iterdir()
            if path.is_dir() and (path / ".agents").is_dir()
        )
    if args.json:
        _dump({"agents": agents})
    else:
        for name in agents:
            print(name)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DotAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for diagnostic in exc.diagnostics:
            print(diagnostic.format(), file=sys.stderr)
        return EXIT_FAILURE
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive
        import os
        import traceback

        if os.environ.get("DOTAGENT_DEBUG"):
            traceback.print_exc()
        print(f"internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
