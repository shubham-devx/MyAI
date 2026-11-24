"""JSON-schema-constrained generation and local validation."""

import json
import re
from typing import Any

from app.providers.base import ModelProvider, ProviderError
from app.schemas import ChatMessage

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json(text: str) -> Any:
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
    raise ValueError("No valid JSON value was found in the model output.")


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


def validate_json(instance: Any, schema: Any, root: dict[str, Any] | None = None, path: str = "$", depth: int = 0) -> list[str]:
    if depth > 40:
        return [f"{path}: schema nesting is too deep."]
    root = root if root is not None else (schema if isinstance(schema, dict) else {})
    schema = _resolve_ref(schema, root)
    if schema is True or schema is None or schema == {}:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here."]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema."]
    errors: list[str] = []

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        if not _type_matches(instance, expected_type):
            return [f"{path}: expected {expected_type}, received {_json_type(instance)}."]
    elif isinstance(expected_type, list):
        if not any(isinstance(item, str) and _type_matches(instance, item) for item in expected_type):
            return [f"{path}: expected one of {', '.join(map(str, expected_type))}."]

    if "enum" in schema and isinstance(schema["enum"], list):
        if instance not in schema["enum"]:
            errors.append(f"{path}: value is not one of the allowed options.")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: value does not match the required constant.")

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
                errors.extend(validate_json(value, properties[key], root, child_path, depth + 1))
            elif additional is False:
                errors.append(f"{path}: unexpected property '{key}'.")
            elif isinstance(additional, dict):
                errors.extend(validate_json(value, additional, root, child_path, depth + 1))
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if isinstance(min_properties, int) and len(instance) < min_properties:
            errors.append(f"{path}: at least {min_properties} properties are required.")
        if isinstance(max_properties, int) and len(instance) > max_properties:
            errors.append(f"{path}: at most {max_properties} properties are allowed.")

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                errors.extend(validate_json(value, items, root, f"{path}[{index}]", depth + 1))
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            errors.append(f"{path}: at least {min_items} items are required.")
        if isinstance(max_items, int) and len(instance) > max_items:
            errors.append(f"{path}: at most {max_items} items are allowed.")
        if schema.get("uniqueItems") is True:
            seen: list[str] = []
            for value in instance:
                rendered = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if rendered in seen:
                    errors.append(f"{path}: items must be unique.")
                    break
                seen.append(rendered)

    if isinstance(instance, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{path}: must contain at least {minimum} characters.")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{path}: must contain at most {maximum} characters.")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and len(pattern) <= 200:
            try:
                if re.search(pattern, instance) is None:
                    errors.append(f"{path}: does not match the required pattern.")
            except re.error:
                pass
        if schema.get("format") == "uri" and not re.match(r"^https?://", instance):
            errors.append(f"{path}: must be an http(s) URI.")

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
        if not isinstance(branches, list):
            continue
        branch_errors = [validate_json(instance, branch, root, path, depth + 1) for branch in branches]
        valid_count = sum(1 for branch in branch_errors if not branch)
        if keyword == "allOf":
            for branch in branch_errors:
                errors.extend(branch)
        elif keyword == "anyOf" and valid_count == 0:
            errors.append(f"{path}: does not match any allowed schema.")
        elif keyword == "oneOf" and valid_count != 1:
            errors.append(f"{path}: must match exactly one allowed schema.")
    if "not" in schema and not validate_json(instance, schema["not"], root, path, depth + 1):
        errors.append(f"{path}: matches a forbidden schema.")

    return errors[:30]


def _instruction(schema: dict[str, Any], system: str) -> str:
    parts = [
        "Return only one valid JSON value. Do not wrap it in markdown fences and do not add commentary.",
        "The JSON must satisfy this JSON Schema exactly:",
        json.dumps(schema, ensure_ascii=False),
    ]
    if system.strip():
        parts.append("Additional task instructions:")
        parts.append(system.strip())
    return "\n".join(parts)


async def generate_structured(
    provider: ModelProvider,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    system: str = "",
) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        raise ProviderError("Provide a JSON Schema object for structured output.", 422)
    messages = [
        ChatMessage(role="system", content=_instruction(schema, system)),
        ChatMessage(role="user", content=prompt),
    ]
    raw = await provider.complete_structured(messages, model, schema)
    errors: list[str] = []
    parsed: Any = None
    attempts = 1
    try:
        parsed = extract_json(raw)
        errors = validate_json(parsed, schema, schema)
    except (ValueError, TypeError) as error:
        errors = [str(error)]
    if errors:
        repair_messages = [
            *messages,
            ChatMessage(role="assistant", content=raw[:20_000]),
            ChatMessage(
                role="user",
                content=(
                    "The previous JSON did not satisfy the schema. Return a corrected JSON value only.\n"
                    "Validation errors:\n" + "\n".join(f"- {item}" for item in errors[:12])
                ),
            ),
        ]
        raw = await provider.complete_structured(repair_messages, model, schema)
        attempts = 2
        try:
            parsed = extract_json(raw)
            errors = validate_json(parsed, schema, schema)
        except (ValueError, TypeError) as error:
            errors = [str(error)]
    if errors:
        raise ProviderError(
            "The model did not return JSON that satisfies the requested schema. Try again or simplify the schema.",
            422,
        )
    return {
        "json": parsed,
        "raw": raw,
        "schema": schema,
        "attempts": attempts,
        "model": model,
        "provider": provider.provider_id,
    }
