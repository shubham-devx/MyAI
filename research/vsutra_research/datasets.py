"""Dataset loading, semantic checks, deduplication, and validation reports.

The workspace standardizes on JSON Lines where each line is one self-contained training
or evaluation example. Validation is deterministic and offline: it never downloads data,
calls a model, or trusts example content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import validate

ALLOWED_ROLES = ("system", "user", "assistant", "tool")
ALLOWED_SPLITS = ("train", "validation", "test")
MAX_MESSAGE_CHARS = 32_000
MAX_EXAMPLES = 500_000

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "generic_credential",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|password|access[_-]?token)\b\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"),
    ),
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\d.])(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?![\d.])")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str) -> str:
    """Normalize text for duplicate detection and deterministic scoring."""
    return re.sub(r"\s+", " ", value.strip().casefold())


@dataclass
class Issue:
    line: int
    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "level": self.level, "code": self.code, "message": self.message}


@dataclass
class DatasetReport:
    dataset: str
    schema: str
    checked_at: str = field(default_factory=_now)
    examples: int = 0
    valid: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    min_examples: int = 1

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "schema": self.schema,
            "checked_at": self.checked_at,
            "summary": {
                "examples": self.examples,
                "valid": self.valid,
                "invalid": self.examples - self.valid,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "passed": not self.errors and self.examples >= self.min_examples,
            },
            "stats": self.stats,
            "duplicates": self.duplicates,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def read_jsonl(path: Path) -> tuple[list[tuple[int, Any]], list[Issue]]:
    records: list[tuple[int, Any]] = []
    issues: list[Issue] = []
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as error:
        return [], [Issue(0, "error", "unreadable_dataset", f"Could not read {path}: {error}")]
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                issues.append(Issue(line_number, "warning", "blank_line", "Blank lines are ignored."))
                continue
            try:
                records.append((line_number, json.loads(text)))
            except json.JSONDecodeError as error:
                issues.append(
                    Issue(line_number, "error", "invalid_json", f"Invalid JSON: {error.msg} at column {error.colno}.")
                )
    return records, issues


def _message_texts(example: dict[str, Any]) -> Iterable[tuple[str, str]]:
    for message in example.get("messages", []):
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            yield str(message.get("role")), message["content"]


def scan_sensitive(example: dict[str, Any]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for role, content in _message_texts(example):
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(("error", f"A possible {name} was detected in a {role} message."))
        if _EMAIL_RE.search(content):
            findings.append(("warning", f"A possible email address was detected in a {role} message."))
        if _PHONE_RE.search(content):
            findings.append(("warning", f"A possible phone number was detected in a {role} message."))
    return findings


def semantic_issues(example: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (level, code, message) for semantic problems not expressible in JSON Schema."""
    problems: list[tuple[str, str, str]] = []
    messages = example.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return [("error", "too_few_messages", "A training example needs at least one user and one assistant message.")]
    if len(messages) > 24:
        problems.append(("error", "too_many_messages", "Examples are limited to 24 messages."))

    roles: list[str] = []
    seen_tool_ids: set[str] = set()
    declared_tool_ids: set[str] = set()
    system_count = 0
    user_count = 0
    assistant_count = 0

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            problems.append(("error", "invalid_message", f"Message {index} is not an object."))
            continue
        role = message.get("role")
        if role not in ALLOWED_ROLES:
            problems.append(("error", "invalid_role", f"Message {index} has an unsupported role."))
            continue
        roles.append(role)
        content = message.get("content", "")
        if not isinstance(content, str):
            problems.append(("error", "invalid_content", f"Message {index} content must be a string."))
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            problems.append(("error", "message_too_long", f"Message {index} exceeds {MAX_MESSAGE_CHARS:,} characters."))

        if role == "system":
            system_count += 1
            if index != 0:
                problems.append(("error", "system_not_first", "System messages may only appear first."))
            if not content.strip():
                problems.append(("warning", "empty_system", "System message is empty."))
        elif role == "user":
            user_count += 1
            if not content.strip():
                problems.append(("error", "empty_user", f"User message {index} is empty."))
        elif role == "assistant":
            assistant_count += 1
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                if not isinstance(tool_calls, list) or not tool_calls:
                    problems.append(("error", "invalid_tool_calls", f"Assistant message {index} has an invalid tool_calls list."))
                else:
                    for call in tool_calls:
                        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not isinstance(call.get("name"), str):
                            problems.append(("error", "invalid_tool_call", f"Assistant message {index} has a malformed tool call."))
                            continue
                        if call["id"] in declared_tool_ids:
                            problems.append(("error", "duplicate_tool_id", f"Tool call ID '{call['id']}' is used more than once."))
                        declared_tool_ids.add(call["id"])
            if not content.strip() and not (isinstance(tool_calls, list) and tool_calls):
                problems.append(("error", "empty_assistant", f"Assistant message {index} has no content or tool calls."))
        elif role == "tool":
            if not content.strip():
                problems.append(("error", "empty_tool_result", f"Tool message {index} is empty."))
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                problems.append(("error", "missing_tool_call_id", f"Tool message {index} needs tool_call_id."))
            elif call_id not in declared_tool_ids:
                problems.append(("error", "unknown_tool_call_id", f"Tool message {index} references an unknown tool call."))
            else:
                seen_tool_ids.add(call_id)
            if not isinstance(message.get("name"), str) or not message.get("name"):
                problems.append(("warning", "missing_tool_name", f"Tool message {index} should include the tool name."))

    if system_count > 1:
        problems.append(("error", "multiple_systems", "Use at most one system message per example."))
    if user_count == 0:
        problems.append(("error", "missing_user", "An example must contain at least one user message."))
    if assistant_count == 0:
        problems.append(("error", "missing_assistant", "An example must contain at least one assistant message."))
    if roles and roles[-1] != "assistant":
        problems.append(("error", "no_final_assistant", "The final message must be an assistant target."))
    if roles and roles[-1] == "assistant" and not str(messages[-1].get("content", "")).strip():
        problems.append(("error", "empty_final_assistant", "The final assistant target must contain text."))
    if declared_tool_ids - seen_tool_ids:
        missing = ", ".join(sorted(declared_tool_ids - seen_tool_ids)[:3])
        problems.append(("warning", "unanswered_tool_call", f"Tool calls without a recorded result: {missing}."))

    for level, message in scan_sensitive(example):
        problems.append((level, "sensitive_content", message))
    return problems


def _prompt_text(example: dict[str, Any]) -> str:
    parts = [
        str(message.get("content", ""))
        for message in example.get("messages", [])
        if isinstance(message, dict) and message.get("role") in {"system", "user"}
    ]
    return "\n".join(parts)


def _assistant_text(example: dict[str, Any]) -> str:
    parts = [
        str(message.get("content", ""))
        for message in example.get("messages", [])
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    return "\n".join(parts)


def _bump(container: dict[str, int], key: str) -> None:
    container[key] = container.get(key, 0) + 1


def validate_dataset(
    dataset_path: str | Path,
    schema_path: str | Path,
    *,
    min_examples: int = 1,
    max_examples: int = MAX_EXAMPLES,
    report_path: str | Path | None = None,
    allow_duplicates: bool = False,
    require_verified: bool = False,
) -> DatasetReport:
    dataset = Path(dataset_path)
    schema_file = Path(schema_path)
    report = DatasetReport(dataset=str(dataset), schema=str(schema_file), min_examples=min_examples)

    try:
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        report.issues.append(Issue(0, "error", "invalid_schema", f"Could not load the JSON Schema: {error}"))
        return report

    records, issues = read_jsonl(dataset)
    report.issues.extend(issues)
    report.examples = len(records)
    if report.examples > max_examples:
        report.issues.append(
            Issue(0, "error", "too_many_examples", f"Dataset exceeds the {max_examples:,} example safety limit.")
        )

    if report.examples < min_examples:
        report.issues.append(
            Issue(0, "error", "too_few_examples", f"Expected at least {min_examples} examples, found {report.examples}.")
        )

    ids: dict[str, int] = {}
    prompt_hashes: dict[str, tuple[int, str]] = {}
    assistant_hashes: dict[str, tuple[int, str]] = {}
    duplicate_ids: list[str] = []
    duplicate_prompts: list[str] = []
    duplicate_answers: list[str] = []

    role_counts: dict[str, int] = {}
    language_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    tool_examples = 0
    verified = 0
    user_chars: list[int] = []
    assistant_chars: list[int] = []
    message_counts: list[int] = []

    for line_number, example in records:
        example_errors = 0
        if not isinstance(example, dict):
            report.issues.append(Issue(line_number, "error", "invalid_example", "Each line must be a JSON object."))
            continue

        schema_errors = validate(example, schema)
        for message in schema_errors[:10]:
            report.issues.append(Issue(line_number, "error", "schema", message))
            example_errors += 1

        for level, code, message in semantic_issues(example):
            report.issues.append(Issue(line_number, level, code, message))
            if level == "error":
                example_errors += 1

        example_id = example.get("id")
        if isinstance(example_id, str) and example_id:
            if example_id in ids:
                duplicate_ids.append(example_id)
                report.issues.append(
                    Issue(line_number, "error", "duplicate_id", f"ID '{example_id}' already appears on line {ids[example_id]}.")
                )
                example_errors += 1
            else:
                ids[example_id] = line_number

        messages = example.get("messages")
        if isinstance(messages, list):
            message_counts.append(len(messages))
            for message in messages:
                if isinstance(message, dict) and isinstance(message.get("role"), str):
                    _bump(role_counts, message["role"])
                    if message.get("role") == "tool" or message.get("tool_calls"):
                        tool_examples += 1
                        break
            for message in messages:
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    if message.get("role") == "user":
                        user_chars.append(len(message["content"]))
                    elif message.get("role") == "assistant":
                        assistant_chars.append(len(message["content"]))

        if isinstance(example.get("language"), str):
            _bump(language_counts, example["language"])
        if isinstance(example.get("split"), str):
            _bump(split_counts, example["split"])
        for tag in example.get("tags") or []:
            if isinstance(tag, str):
                _bump(tag_counts, tag)
        metadata = example.get("metadata")
        if isinstance(metadata, dict) and metadata.get("verified") is True:
            verified += 1
        elif require_verified:
            report.issues.append(
                Issue(line_number, "error", "unverified", "Example is not marked metadata.verified = true.")
            )
            example_errors += 1

        prompt = normalize_text(_prompt_text(example))
        if prompt:
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if prompt_hash in prompt_hashes:
                first_line, first_id = prompt_hashes[prompt_hash]
                duplicate_prompts.append(str(example.get("id", line_number)))
                report.issues.append(
                    Issue(line_number, "error", "duplicate_prompt", f"Prompt duplicates line {first_line} (id {first_id}).")
                )
                example_errors += 1
            else:
                prompt_hashes[prompt_hash] = (line_number, str(example.get("id", line_number)))
        answer = normalize_text(_assistant_text(example))
        if answer:
            answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
            if answer_hash in assistant_hashes:
                duplicate_answers.append(str(example.get("id", line_number)))
                report.issues.append(
                    Issue(line_number, "warning", "duplicate_answer", "Assistant target text duplicates another example.")
                )
            else:
                assistant_hashes[answer_hash] = (line_number, str(example.get("id", line_number)))

        if example_errors == 0:
            report.valid += 1

    if allow_duplicates:
        for issue in report.issues:
            if issue.code in {"duplicate_id", "duplicate_prompt"}:
                issue.level = "warning"

    if duplicate_ids:
        report.duplicates["ids"] = duplicate_ids
    if duplicate_prompts:
        report.duplicates["prompts"] = duplicate_prompts
    if duplicate_answers:
        report.duplicates["answers"] = duplicate_answers

    total_user = sum(user_chars)
    total_assistant = sum(assistant_chars)
    report.stats = {
        "roles": role_counts,
        "languages": language_counts,
        "splits": split_counts,
        "tags": dict(sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "tool_examples": tool_examples,
        "verified_examples": verified,
        "message_count": {
            "min": min(message_counts) if message_counts else 0,
            "max": max(message_counts) if message_counts else 0,
            "avg": round(sum(message_counts) / len(message_counts), 2) if message_counts else 0,
        },
        "user_chars": {"total": total_user, "avg": round(total_user / len(user_chars), 1) if user_chars else 0, "max": max(user_chars, default=0)},
        "assistant_chars": {"total": total_assistant, "avg": round(total_assistant / len(assistant_chars), 1) if assistant_chars else 0, "max": max(assistant_chars, default=0)},
        "approx_total_tokens": int((total_user + total_assistant) / 4),
        "approx_tokens_per_example": int(((total_user + total_assistant) / 4) / report.examples) if report.examples else 0,
    }

    if report_path is not None:
        write_report(report, report_path)
    return report


def write_report(report: DatasetReport, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def format_summary(report: DatasetReport, limit: int = 12) -> str:
    summary = report.as_dict()["summary"]
    lines = [
        f"Dataset: {report.dataset}",
        f"Schema:  {report.schema}",
        f"Examples: {summary['examples']} · valid {summary['valid']} · errors {summary['errors']} · warnings {summary['warnings']}",
        f"Approx tokens: {report.stats.get('approx_total_tokens', 0):,} (estimate at 4 characters per token)",
    ]
    if report.duplicates:
        lines.append("Duplicates: " + ", ".join(f"{key}={len(value)}" for key, value in report.duplicates.items()))
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for issue in report.errors[:limit]:
            lines.append(f"  line {issue.line}: [{issue.code}] {issue.message}")
        if len(report.errors) > limit:
            lines.append(f"  … {len(report.errors) - limit} more errors")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for issue in report.warnings[:limit]:
            lines.append(f"  line {issue.line}: [{issue.code}] {issue.message}")
        if len(report.warnings) > limit:
            lines.append(f"  … {len(report.warnings) - limit} more warnings")
    lines.append("")
    lines.append("PASS" if not report.errors and report.examples >= report.min_examples else "FAIL")
    return "\n".join(lines)
