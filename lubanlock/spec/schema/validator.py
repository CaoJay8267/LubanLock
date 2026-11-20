"""A small, strict JSON Schema (2020-12 subset) validator.

LubanLock needs schema validation for two different jobs:

* validating ``.agent/`` documents against its own shipped schemas, and
* validating (and linting) the JSON Schema embedded in tool declarations.

Instead of depending on a third-party validator, LubanLock implements the
subset it documents.  Unsupported keywords are rejected by
:func:`lint_json_schema` rather than silently ignored, so a tool author never
believes a constraint is enforced when it is not.

Supported keywords
------------------
``$ref`` (local and sibling-file), ``$defs``, ``type``, ``enum``, ``const``,
``required``, ``properties``, ``patternProperties``, ``propertyNames``,
``additionalProperties``, ``items``, ``prefixItems``, ``contains``,
``minContains``/``maxContains``, ``dependentRequired``, ``dependentSchemas``,
``minItems``/``maxItems``/``uniqueItems``, ``minLength``/``maxLength``/
``pattern``, ``minimum``/``maximum``/``exclusiveMinimum``/
``exclusiveMaximum``/``multipleOf``, ``minProperties``/``maxProperties``,
``if``/``then``/``else``, ``allOf``/``anyOf``/``oneOf``/``not`` and boolean
schemas.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lubanlock.core.errors import Diagnostic, Severity

_JSON_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}

_ANNOTATION_KEYWORDS = {
    "$schema",
    "$id",
    "$anchor",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
    "comment",
}

SUPPORTED_KEYWORDS = {
    "$ref",
    "$defs",
    "type",
    "enum",
    "const",
    "required",
    "properties",
    "patternProperties",
    "propertyNames",
    "additionalProperties",
    "items",
    "prefixItems",
    "contains",
    "minContains",
    "maxContains",
    "dependentRequired",
    "dependentSchemas",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minProperties",
    "maxProperties",
    "if",
    "then",
    "else",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
} | _ANNOTATION_KEYWORDS

_CODE_REQUIRED = "DTA201"
_CODE_TYPE = "DTA202"
_CODE_ADDITIONAL = "DTA203"
_CODE_ENUM = "DTA204"
_CODE_STRING = "DTA205"
_CODE_NUMBER = "DTA206"
_CODE_COMPOSITION = "DTA207"
_CODE_ARRAY = "DTA208"
_CODE_OBJECT = "DTA209"
_CODE_REF = "DTA210"


class SchemaResolutionError(ValueError):
    """A ``$ref`` could not be resolved."""


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_local(document: dict, ref: str) -> dict | bool:
    """Resolve a ``#/...`` JSON pointer against *document*."""

    if not ref.startswith("#"):
        raise SchemaResolutionError(f"not a local reference: {ref!r}")
    fragment = ref[1:]
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise SchemaResolutionError(f"unsupported local $ref fragment: {ref!r}")
    node: object = document
    for raw in fragment.lstrip("/").split("/"):
        token = _unescape(raw)
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise SchemaResolutionError(f"$ref {ref!r} does not resolve")
    if not isinstance(node, (dict, bool)):
        raise SchemaResolutionError(f"$ref {ref!r} does not point at a schema")
    return node


def _json_equal(left: object, right: object) -> bool:
    # Distinguishes 1 from True and 1 from 1.0, matching JSON semantics.
    return type(left) is type(right) and left == right or (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
        and left == right
    )


def _type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


class SchemaStore:
    """Loads and resolves LubanLock's bundled JSON Schema files."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[Path, dict] = {}

    def path_for(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise SchemaResolutionError(f"schema {name!r} escapes the schema directory")
        if not path.is_file():
            raise SchemaResolutionError(f"unknown schema {name!r}")
        return path

    def load(self, name: str) -> dict:
        return self._load_path(self.path_for(name))

    def _load_path(self, path: Path) -> dict:
        resolved = path.resolve()
        if resolved not in self._cache:
            document = json.loads(resolved.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise SchemaResolutionError(f"schema {resolved} is not a JSON object")
            self._cache[resolved] = document
        return self._cache[resolved]

    def resolve(self, ref: str, base_file: Path) -> tuple[Path, dict | bool, dict]:
        """Resolve an external *ref* into (file, subschema, root document)."""

        target = ref
        fragment = ""
        if "#" in ref:
            target, fragment = ref.split("#", 1)
        if target:
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
                raise SchemaResolutionError(
                    f"absolute $ref {ref!r} is not supported; use a relative file reference"
                )
            schema_file = (base_file.parent / target).resolve()
            if not schema_file.is_relative_to(self.root.resolve()):
                raise SchemaResolutionError(f"$ref {ref!r} escapes the schema directory")
        else:
            schema_file = base_file.resolve()
        document = self._load_path(schema_file)
        node: object = document
        if fragment:
            node = _resolve_local(document, "#" + fragment)
        if not isinstance(node, (dict, bool)):
            raise SchemaResolutionError(f"$ref {ref!r} does not point at a schema")
        return schema_file, node, document


def validate_instance(
    instance: object,
    schema: dict | bool,
    *,
    store: SchemaStore,
    base_file: Path,
    pointer: str = "",
    seen: frozenset[tuple[str, int, str]] | None = None,
    document: dict | None = None,
) -> list[Diagnostic]:
    """Validate *instance* and return every violation found.

    ``document`` is the root schema document that local ``#`` references
    resolve against.  It defaults to *schema* itself, which also makes inline
    tool schemas (never written to disk) work.
    """

    diagnostics: list[Diagnostic] = []

    if schema is True:
        return diagnostics
    if schema is False:
        return [
            Diagnostic(
                _CODE_REF,
                "no value is allowed here (schema is false)",
                Severity.ERROR,
                pointer=pointer,
            )
        ]
    if not isinstance(schema, dict):
        return [
            Diagnostic(
                _CODE_REF,
                "malformed schema: expected an object or boolean",
                Severity.ERROR,
                pointer=pointer,
            )
        ]

    if document is None:
        document = schema

    key = (str(base_file), id(schema), pointer)
    if seen is None:
        seen = frozenset()
    if key in seen:
        return diagnostics
    seen = seen | {key}

    if "$ref" in schema:
        ref = str(schema["$ref"])
        target_file = base_file
        target_document = document
        try:
            if ref.startswith("#"):
                target = _resolve_local(document, ref)
            else:
                target_file, target, target_document = store.resolve(ref, base_file)
        except SchemaResolutionError as exc:
            return [
                Diagnostic(
                    _CODE_REF,
                    f"cannot resolve $ref: {exc}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            ]
        diagnostics.extend(
            validate_instance(
                instance,
                target,
                store=store,
                base_file=target_file,
                pointer=pointer,
                seen=seen,
                document=target_document if isinstance(target_document, dict) else None,
            )
        )
        remainder = {
            k: v
            for k, v in schema.items()
            if k not in _ANNOTATION_KEYWORDS and k not in {"$ref", "$defs"}
        }
        if not remainder:
            return diagnostics
        schema = remainder

    if "type" in schema:
        expected = schema["type"]
        candidates = [expected] if isinstance(expected, str) else list(expected)
        if not any(
            isinstance(candidate, str)
            and candidate in _JSON_TYPES
            and _type_matches(instance, candidate)
            for candidate in candidates
        ):
            names = " or ".join(candidates)
            diagnostics.append(
                Diagnostic(
                    _CODE_TYPE,
                    f"expected {names}, got {_describe_type(instance)}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
            return diagnostics

    if "const" in schema and not _json_equal(instance, schema["const"]):
        diagnostics.append(
            Diagnostic(
                _CODE_ENUM,
                f"expected {_short_json(schema['const'])}, got {_short_json(instance)}",
                Severity.ERROR,
                pointer=pointer,
            )
        )

    if "enum" in schema:
        allowed = schema["enum"]
        if isinstance(allowed, list) and not any(
            _json_equal(instance, candidate) for candidate in allowed
        ):
            rendered = ", ".join(_short_json(v) for v in allowed[:8])
            diagnostics.append(
                Diagnostic(
                    _CODE_ENUM,
                    f"{_short_json(instance)} is not one of: {rendered}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )

    if isinstance(instance, str):
        diagnostics.extend(_validate_string(instance, schema, pointer))
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        diagnostics.extend(_validate_number(instance, schema, pointer))
    elif isinstance(instance, list):
        diagnostics.extend(
            _validate_array(instance, schema, pointer, store, base_file, seen, document)
        )
    elif isinstance(instance, dict):
        diagnostics.extend(
            _validate_object(instance, schema, pointer, store, base_file, seen, document)
        )

    diagnostics.extend(
        _validate_composition(instance, schema, pointer, store, base_file, seen, document)
    )
    return diagnostics


def _validate_string(instance: str, schema: dict, pointer: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "minLength" in schema and len(instance) < schema["minLength"]:
        diagnostics.append(
            Diagnostic(
                _CODE_STRING,
                f"string is {len(instance)} characters, minimum is {schema['minLength']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "maxLength" in schema and len(instance) > schema["maxLength"]:
        diagnostics.append(
            Diagnostic(
                _CODE_STRING,
                f"string is {len(instance)} characters, maximum is {schema['maxLength']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "pattern" in schema and isinstance(schema["pattern"], str):
        try:
            matched = re.search(schema["pattern"], instance) is not None
        except re.error:  # pragma: no cover - lint catches this first
            matched = True
        if not matched:
            diagnostics.append(
                Diagnostic(
                    _CODE_STRING,
                    f"{_short_json(instance)} does not match pattern {schema['pattern']!r}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
    return diagnostics


def _validate_number(instance: float, schema: dict, pointer: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "minimum" in schema and instance < schema["minimum"]:
        diagnostics.append(
            Diagnostic(
                _CODE_NUMBER,
                f"{instance} is less than minimum {schema['minimum']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "maximum" in schema and instance > schema["maximum"]:
        diagnostics.append(
            Diagnostic(
                _CODE_NUMBER,
                f"{instance} is greater than maximum {schema['maximum']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
        diagnostics.append(
            Diagnostic(
                _CODE_NUMBER,
                f"{instance} must be greater than {schema['exclusiveMinimum']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
        diagnostics.append(
            Diagnostic(
                _CODE_NUMBER,
                f"{instance} must be less than {schema['exclusiveMaximum']}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "multipleOf" in schema and schema["multipleOf"] > 0:
        ratio = instance / schema["multipleOf"]
        if abs(ratio - round(ratio)) > 1e-9:
            diagnostics.append(
                Diagnostic(
                    _CODE_NUMBER,
                    f"{instance} is not a multiple of {schema['multipleOf']}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
    return diagnostics


def _validate_array(
    instance: list,
    schema: dict,
    pointer: str,
    store: SchemaStore,
    base_file: Path,
    seen: frozenset[tuple[str, int, str]],
    document: dict,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "minItems" in schema and len(instance) < schema["minItems"]:
        diagnostics.append(
            Diagnostic(
                _CODE_ARRAY,
                f"expected at least {schema['minItems']} items, got {len(instance)}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "maxItems" in schema and len(instance) > schema["maxItems"]:
        diagnostics.append(
            Diagnostic(
                _CODE_ARRAY,
                f"expected at most {schema['maxItems']} items, got {len(instance)}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if schema.get("uniqueItems"):
        first_seen: dict[str, int] = {}
        for index, item in enumerate(instance):
            marker = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            if marker in first_seen:
                diagnostics.append(
                    Diagnostic(
                        _CODE_ARRAY,
                        f"items at index {first_seen[marker]} and {index} are duplicates",
                        Severity.ERROR,
                        pointer=pointer,
                    )
                )
                break
            first_seen[marker] = index
    prefix_items = schema.get("prefixItems")
    start = 0
    if isinstance(prefix_items, list):
        for index, subschema in enumerate(prefix_items):
            if index >= len(instance):
                break
            diagnostics.extend(
                validate_instance(
                    instance[index],
                    subschema,
                    store=store,
                    base_file=base_file,
                    pointer=f"{pointer}/{index}",
                    seen=seen,
                    document=document,
                )
            )
        start = len(prefix_items)
    items = schema.get("items")
    if isinstance(items, (dict, bool)):
        for index in range(start, len(instance)):
            diagnostics.extend(
                validate_instance(
                    instance[index],
                    items,
                    store=store,
                    base_file=base_file,
                    pointer=f"{pointer}/{index}",
                    seen=seen,
                    document=document,
                )
            )
    if "contains" in schema:
        contains = schema["contains"]
        matches = [
            index
            for index, item in enumerate(instance)
            if not validate_instance(
                item,
                contains,
                store=store,
                base_file=base_file,
                pointer=f"{pointer}/{index}",
                seen=seen,
                document=document,
            )
        ]
        minimum = schema.get("minContains", 1)
        maximum = schema.get("maxContains")
        if len(matches) < minimum:
            diagnostics.append(
                Diagnostic(
                    _CODE_ARRAY,
                    f"expected at least {minimum} item(s) matching 'contains', "
                    f"got {len(matches)}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
        if maximum is not None and len(matches) > maximum:
            diagnostics.append(
                Diagnostic(
                    _CODE_ARRAY,
                    f"expected at most {maximum} item(s) matching 'contains', "
                    f"got {len(matches)}",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
    return diagnostics


def _validate_object(
    instance: dict,
    schema: dict,
    pointer: str,
    store: SchemaStore,
    base_file: Path,
    seen: frozenset[tuple[str, int, str]],
    document: dict,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "minProperties" in schema and len(instance) < schema["minProperties"]:
        diagnostics.append(
            Diagnostic(
                _CODE_OBJECT,
                f"expected at least {schema['minProperties']} properties, got {len(instance)}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
        diagnostics.append(
            Diagnostic(
                _CODE_OBJECT,
                f"expected at most {schema['maxProperties']} properties, got {len(instance)}",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if key not in instance:
                diagnostics.append(
                    Diagnostic(
                        _CODE_REQUIRED,
                        f"missing required property {key!r}",
                        Severity.ERROR,
                        pointer=pointer,
                    )
                )
    properties = schema.get("properties", {})
    matched_keys: set[str] = set()
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in instance:
                matched_keys.add(key)
                diagnostics.extend(
                    validate_instance(
                        instance[key],
                        subschema,
                        store=store,
                        base_file=base_file,
                        pointer=f"{pointer}/{_escape(key)}",
                        seen=seen,
                        document=document,
                    )
                )
    pattern_properties = schema.get("patternProperties", {})
    if isinstance(pattern_properties, dict):
        for pattern, subschema in pattern_properties.items():
            try:
                regex = re.compile(pattern)
            except re.error:  # pragma: no cover - lint reports this first
                continue
            for key, value in instance.items():
                if regex.search(key):
                    matched_keys.add(key)
                    diagnostics.extend(
                        validate_instance(
                            value,
                            subschema,
                            store=store,
                            base_file=base_file,
                            pointer=f"{pointer}/{_escape(key)}",
                            seen=seen,
                            document=document,
                        )
                    )
    if "propertyNames" in schema:
        names_schema = schema["propertyNames"]
        for key in instance:
            diagnostics.extend(
                validate_instance(
                    key,
                    names_schema,
                    store=store,
                    base_file=base_file,
                    pointer=f"{pointer}/{_escape(key)}",
                    seen=seen,
                    document=document,
                )
            )
    dependent_required = schema.get("dependentRequired")
    if isinstance(dependent_required, dict):
        for key, required_keys in dependent_required.items():
            if key not in instance or not isinstance(required_keys, list):
                continue
            for required_key in required_keys:
                if required_key not in instance:
                    diagnostics.append(
                        Diagnostic(
                            _CODE_REQUIRED,
                            f"property {required_key!r} is required when {key!r} is present",
                            Severity.ERROR,
                            pointer=pointer,
                        )
                    )
    dependent_schemas = schema.get("dependentSchemas")
    if isinstance(dependent_schemas, dict):
        for key, subschema in dependent_schemas.items():
            if key in instance:
                diagnostics.extend(
                    validate_instance(
                        instance,
                        subschema,
                        store=store,
                        base_file=base_file,
                        pointer=pointer,
                        seen=seen,
                        document=document,
                    )
                )
    additional = schema.get("additionalProperties", True)
    extras = [
        key
        for key in instance
        if key not in properties and key not in matched_keys
    ]
    if additional is False:
        for key in extras:
            diagnostics.append(
                Diagnostic(
                    _CODE_ADDITIONAL,
                    (
                        f"unexpected property {key!r}"
                        + _allowed_hint(list(properties))
                    ),
                    Severity.ERROR,
                    pointer=f"{pointer}/{_escape(key)}",
                )
            )
    elif isinstance(additional, (dict, bool)):
        for key in extras:
            diagnostics.extend(
                validate_instance(
                    instance[key],
                    additional,
                    store=store,
                    base_file=base_file,
                    pointer=f"{pointer}/{_escape(key)}",
                    seen=seen,
                    document=document,
                )
            )
    return diagnostics


def _validate_composition(
    instance: object,
    schema: dict,
    pointer: str,
    store: SchemaStore,
    base_file: Path,
    seen: frozenset[tuple[str, int, str]],
    document: dict,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "if" in schema:
        condition = not validate_instance(
            instance,
            schema["if"],
            store=store,
            base_file=base_file,
            pointer=pointer,
            seen=seen,
            document=document,
        )
        branch = schema.get("then") if condition else schema.get("else")
        if isinstance(branch, (dict, bool)):
            diagnostics.extend(
                validate_instance(
                    instance,
                    branch,
                    store=store,
                    base_file=base_file,
                    pointer=pointer,
                    seen=seen,
                    document=document,
                )
            )
    for keyword in ("allOf",):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                diagnostics.extend(
                    validate_instance(
                        instance,
                        branch,
                        store=store,
                        base_file=base_file,
                        pointer=pointer,
                        seen=seen,
                        document=document,
                    )
                )
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        matches = sum(
            1
            for branch in branches
            if not validate_instance(
                instance,
                branch,
                store=store,
                base_file=base_file,
                pointer=pointer,
                seen=seen,
                document=document,
            )
        )
        if matches == 0:
            diagnostics.append(
                Diagnostic(
                    _CODE_COMPOSITION,
                    f"value does not match any of the {keyword} branches",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
        elif keyword == "oneOf" and matches > 1:
            diagnostics.append(
                Diagnostic(
                    _CODE_COMPOSITION,
                    f"value matches {matches} oneOf branches; exactly one is required",
                    Severity.ERROR,
                    pointer=pointer,
                )
            )
    if "not" in schema and not validate_instance(
        instance,
        schema["not"],
        store=store,
        base_file=base_file,
        pointer=pointer,
        seen=seen,
        document=document,
    ):
        diagnostics.append(
            Diagnostic(
                _CODE_COMPOSITION,
                "value matches a schema it must not match",
                Severity.ERROR,
                pointer=pointer,
            )
        )
    return diagnostics


def _allowed_hint(names: list[str]) -> str:
    if not names:
        return ""
    shown = ", ".join(sorted(names)[:10])
    suffix = ", ..." if len(names) > 10 else ""
    return f" (allowed: {shown}{suffix})"


def _describe_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__  # pragma: no cover - defensive


def _short_json(value: object, limit: int = 120) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        rendered = repr(value)
    if len(rendered) > limit:
        return rendered[: limit - 3] + "..."
    return rendered


def lint_json_schema(
    schema: object,
    *,
    path: str | None = None,
    pointer: str = "",
    local_only: bool = True,
) -> list[Diagnostic]:
    """Check that *schema* only uses keywords LubanLock can actually enforce."""

    diagnostics: list[Diagnostic] = []
    _lint_schema_node(schema, diagnostics, path, pointer, local_only)
    return diagnostics


def _lint_schema_node(
    schema: object,
    diagnostics: list[Diagnostic],
    path: str | None,
    pointer: str,
    local_only: bool,
) -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        diagnostics.append(
            Diagnostic(
                "DTA308",
                "schema must be an object or boolean",
                Severity.ERROR,
                path,
                pointer,
            )
        )
        return
    for keyword in schema:
        if keyword not in SUPPORTED_KEYWORDS:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"unsupported JSON Schema keyword {keyword!r}",
                    Severity.ERROR,
                    path,
                    pointer,
                    hint="LubanLock supports a documented subset; see 'lubanlock schema --help'.",
                )
            )
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str):
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    "$ref must be a string",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
        elif local_only and not ref.startswith("#"):
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"tool schemas may only use local $ref, got {ref!r}",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    expected_types = {
        "type": (str, list),
        "enum": (list,),
        "const": (object,),
        "required": (list,),
        "properties": (dict,),
        "patternProperties": (dict,),
        "propertyNames": (dict, bool),
        "additionalProperties": (bool, dict),
        "items": (dict, list),
        "prefixItems": (list,),
        "contains": (dict, bool),
        "minContains": (int,),
        "maxContains": (int,),
        "dependentRequired": (dict,),
        "dependentSchemas": (dict,),
        "minItems": (int,),
        "maxItems": (int,),
        "uniqueItems": (bool,),
        "minLength": (int,),
        "maxLength": (int,),
        "pattern": (str,),
        "minimum": (int, float),
        "maximum": (int, float),
        "exclusiveMinimum": (int, float),
        "exclusiveMaximum": (int, float),
        "multipleOf": (int, float),
        "minProperties": (int,),
        "maxProperties": (int,),
        "if": (dict, bool),
        "then": (dict, bool),
        "else": (dict, bool),
        "allOf": (list,),
        "anyOf": (list,),
        "oneOf": (list,),
        "not": (dict, bool),
    }
    for keyword, allowed_types in expected_types.items():
        if keyword in schema and not isinstance(schema[keyword], allowed_types):
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"{keyword} has an invalid type ({type(schema[keyword]).__name__})",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    type_value = schema.get("type")
    if isinstance(type_value, str):
        type_list = [type_value]
    elif isinstance(type_value, list):
        type_list = type_value
    else:
        type_list = []
    for candidate in type_list:
        if not isinstance(candidate, str) or candidate not in _JSON_TYPES:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"unknown JSON type {candidate!r}",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    for keyword in (
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minProperties",
        "maxProperties",
        "minContains",
        "maxContains",
    ):
        if isinstance(schema.get(keyword), int) and schema[keyword] < 0:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"{keyword} must be >= 0",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    min_contains = schema.get("minContains")
    max_contains = schema.get("maxContains")
    if (
        isinstance(min_contains, int)
        and isinstance(max_contains, int)
        and min_contains > max_contains
    ):
        diagnostics.append(
            Diagnostic(
                "DTA308",
                "minContains must be <= maxContains",
                Severity.ERROR,
                path,
                pointer,
            )
        )
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            re.compile(pattern)
        except re.error as exc:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"invalid regular expression: {exc}",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    pattern_properties = schema.get("patternProperties")
    if isinstance(pattern_properties, dict):
        for pattern_key in pattern_properties:
            try:
                re.compile(pattern_key)
            except re.error as exc:
                diagnostics.append(
                    Diagnostic(
                        "DTA308",
                        f"invalid patternProperties key {pattern_key!r}: {exc}",
                        Severity.ERROR,
                        path,
                        pointer,
                    )
                )
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and not branches:
            diagnostics.append(
                Diagnostic(
                    "DTA308",
                    f"{keyword} must contain at least one schema",
                    Severity.ERROR,
                    path,
                    pointer,
                )
            )
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            _lint_schema_node(
                value, diagnostics, path, f"{pointer}/properties/{_escape(key)}", local_only
            )
    pattern_properties = schema.get("patternProperties")
    if isinstance(pattern_properties, dict):
        for key, value in pattern_properties.items():
            _lint_schema_node(
                value,
                diagnostics,
                path,
                f"{pointer}/patternProperties/{_escape(key)}",
                local_only,
            )
    if "propertyNames" in schema:
        _lint_schema_node(
            schema["propertyNames"],
            diagnostics,
            path,
            f"{pointer}/propertyNames",
            local_only,
        )
    if "contains" in schema:
        _lint_schema_node(
            schema["contains"], diagnostics, path, f"{pointer}/contains", local_only
        )
    if "prefixItems" in schema and isinstance(schema["prefixItems"], list):
        for index, item in enumerate(schema["prefixItems"]):
            _lint_schema_node(
                item, diagnostics, path, f"{pointer}/prefixItems/{index}", local_only
            )
    if "dependentSchemas" in schema and isinstance(schema["dependentSchemas"], dict):
        for key, value in schema["dependentSchemas"].items():
            _lint_schema_node(
                value,
                diagnostics,
                path,
                f"{pointer}/dependentSchemas/{_escape(key)}",
                local_only,
            )
    if "if" in schema:
        _lint_schema_node(
            schema["if"], diagnostics, path, f"{pointer}/if", local_only
        )
    for keyword in ("then", "else"):
        if keyword in schema:
            _lint_schema_node(
                schema[keyword], diagnostics, path, f"{pointer}/{keyword}", local_only
            )
    required = schema.get("required")
    if isinstance(required, list):
        for index, key in enumerate(required):
            if not isinstance(key, str):
                diagnostics.append(
                    Diagnostic(
                        "DTA308",
                        f"required[{index}] must be a string",
                        Severity.ERROR,
                        path,
                        pointer,
                    )
                )
    dependent_required = schema.get("dependentRequired")
    if isinstance(dependent_required, dict):
        for key, required_keys in dependent_required.items():
            if not isinstance(required_keys, list) or not all(
                isinstance(item, str) for item in required_keys
            ):
                diagnostics.append(
                    Diagnostic(
                        "DTA308",
                        f"dependentRequired[{key!r}] must be an array of strings",
                        Severity.ERROR,
                        path,
                        pointer,
                    )
                )
    items = schema.get("items")
    if isinstance(items, dict):
        _lint_schema_node(items, diagnostics, path, f"{pointer}/items", local_only)
    elif isinstance(items, list):
        for index, item in enumerate(items):
            _lint_schema_node(
                item, diagnostics, path, f"{pointer}/items/{index}", local_only
            )
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        _lint_schema_node(
            additional, diagnostics, path, f"{pointer}/additionalProperties", local_only
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for index, branch in enumerate(branches):
                _lint_schema_node(
                    branch, diagnostics, path, f"{pointer}/{keyword}/{index}", local_only
                )
    if "not" in schema:
        _lint_schema_node(
            schema["not"], diagnostics, path, f"{pointer}/not", local_only
        )
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for key, value in defs.items():
            _lint_schema_node(
                value, diagnostics, path, f"{pointer}/$defs/{_escape(key)}", local_only
            )
