"""Minimal JSON Schema validation for the research workspace.

The workspace deliberately does not depend on a full JSON Schema library so that dataset
validation and structured-output scoring work in a bare Python environment. This module
implements the subset used by V-Sutra research artifacts: object/array/scalar types,
required properties, additionalProperties, items, enums, constants, string and numeric
bounds, pattern matching, unique items, and local ``$ref`` resolution.

It is a validation aid, not a general-purpose JSON Schema implementation. Unsupported
keywords are ignored rather than treated as satisfied silently: callers should keep
schemas within the documented subset.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_DEPTH = 50
MAX_PATTERN_CHARS = 200
MAX_ERRORS = 40


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _type_matches(value: Any, expected: str) -> bool:
    actual = _json_type(value)
    if expected == "number":
        return actual in {"number", "integer"}
    if expected == "integer":
        return actual == "integer"
    return actual == expected


def _resolve_ref(schema: Any, root: dict[str, Any]) -> Any:
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: Any = root
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return schema
        current = current[part]
    return current


def validate(
    instance: Any,
    schema: Any,
    root: dict[str, Any] | None = None,
    path: str = "$",
    depth: int = 0,
) -> list[str]:
    """Return human-readable validation errors. An empty list means the value matches."""
    if depth > MAX_DEPTH:
        return [f"{path}: schema nesting exceeds the supported depth."]
    root = root if root is not None else (schema if isinstance(schema, dict) else {})
    schema = _resolve_ref(schema, root)

    if schema is True or schema is None or schema == {}:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here."]
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object or boolean."]

    errors: list[str] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        if not _type_matches(instance, expected_type):
            return [f"{path}: expected {expected_type}, received {_json_type(instance)}."]
    elif isinstance(expected_type, list):
        allowed = [item for item in expected_type if isinstance(item, str)]
        if allowed and not any(_type_matches(instance, item) for item in allowed):
            return [f"{path}: expected one of {', '.join(allowed)}, received {_json_type(instance)}."]

    if "enum" in schema and isinstance(schema["enum"], list) and instance not in schema["enum"]:
        errors.append(f"{path}: value is not one of the allowed options.")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: value does not equal the required constant.")

    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in instance:
                    errors.append(f"{path}: missing required property '{key}'.")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate(value, properties[key], root, child_path, depth + 1))
            elif additional is False:
                errors.append(f"{path}: unexpected property '{key}'.")
            elif isinstance(additional, dict):
                errors.extend(validate(value, additional, root, child_path, depth + 1))
        minimum = schema.get("minProperties")
        maximum = schema.get("maxProperties")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{path}: at least {minimum} properties are required.")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{path}: at most {maximum} properties are allowed.")

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict) or isinstance(items, bool):
            for index, value in enumerate(instance):
                errors.extend(validate(value, items, root, f"{path}[{index}]", depth + 1))
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{path}: at least {minimum} items are required.")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{path}: at most {maximum} items are allowed.")
        if schema.get("uniqueItems") is True:
            seen: set[str] = set()
            for value in instance:
                rendered = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if rendered in seen:
                    errors.append(f"{path}: items must be unique.")
                    break
                seen.add(rendered)

    if isinstance(instance, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{path}: must contain at least {minimum} characters.")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{path}: must contain at most {maximum} characters.")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and len(pattern) <= MAX_PATTERN_CHARS:
            try:
                if re.search(pattern, instance) is None:
                    errors.append(f"{path}: does not match the required pattern.")
            except re.error:
                errors.append(f"{path}: schema contains an invalid pattern.")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{path}: must be >= {minimum}.")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"{path}: must be <= {maximum}.")
        if isinstance(exclusive_minimum, (int, float)) and instance <= exclusive_minimum:
            errors.append(f"{path}: must be > {exclusive_minimum}.")
        if isinstance(exclusive_maximum, (int, float)) and instance >= exclusive_maximum:
            errors.append(f"{path}: must be < {exclusive_maximum}.")

    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list) or not branches:
            continue
        branch_errors = [validate(instance, branch, root, path, depth + 1) for branch in branches]
        valid_count = sum(1 for item in branch_errors if not item)
        if keyword == "allOf":
            for item in branch_errors:
                errors.extend(item)
        elif keyword == "anyOf" and valid_count == 0:
            errors.append(f"{path}: does not match any allowed schema.")
        elif keyword == "oneOf" and valid_count != 1:
            errors.append(f"{path}: must match exactly one allowed schema.")
    if "not" in schema and not validate(instance, schema["not"], root, path, depth + 1):
        errors.append(f"{path}: matches a forbidden schema.")

    return errors[:MAX_ERRORS]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json(text: str) -> Any:
    """Extract the first JSON value from a model response, tolerating code fences."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON value was found in the text.")
