"""Deterministic evaluation for instruction-following and tool-use behavior.

The framework scores real model responses or a caller-supplied predictions file. It never
invents a response and never claims a score for a case that was not executed. Rubric cases
are marked as manual and excluded from the automatic accuracy denominator.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .inference import ChatResult, InferenceError, build_backend
from .schema import extract_json, validate

MAX_REGEX_CHARS = 300


@dataclass
class EvalCase:
    id: str
    prompt: str
    expected: dict[str, Any]
    system: str = ""
    tags: list[str] = field(default_factory=list)
    max_tokens: int | None = None


@dataclass
class EvalResult:
    case: EvalCase
    status: str
    score: float | None
    reason: str
    prediction: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "tags": self.case.tags,
            "expected_type": self.case.expected.get("type"),
            "status": self.status,
            "score": self.score,
            "reason": self.reason,
            "prediction_excerpt": self.prediction[:2000],
            "tool_calls": self.tool_calls,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "error": self.error,
        }


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _accepted_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _arguments_subset(expected: Any, actual: Any, path: str = "arguments") -> str | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path} must be an object."
        for key, value in expected.items():
            if key not in actual:
                return f"{path}.{key} is missing."
            problem = _arguments_subset(value, actual[key], f"{path}.{key}")
            if problem:
                return problem
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{path} must be an array."
        if len(expected) != len(actual):
            return f"{path} must contain {len(expected)} items."
        for index, (left, right) in enumerate(zip(expected, actual)):
            problem = _arguments_subset(left, right, f"{path}[{index}]")
            if problem:
                return problem
        return None
    if expected != actual:
        return f"{path} does not match the expected value."
    return None


def score_case(case: EvalCase, prediction: str, tool_calls: list[dict[str, Any]] | None = None) -> tuple[str, float | None, str]:
    expected = case.expected
    kind = str(expected.get("type", "")).lower()
    tool_calls = tool_calls or []

    if kind == "exact":
        accepted = _accepted_values(expected.get("value"))
        if not accepted:
            return "fail", 0.0, "The case does not define an accepted exact value."
        if normalize(prediction) in {normalize(item) for item in accepted}:
            return "pass", 1.0, "The normalized response exactly matches an accepted value."
        return "fail", 0.0, "The normalized response does not match the accepted value."

    if kind == "contains":
        required = _accepted_values(expected.get("value"))
        if not required:
            return "fail", 0.0, "The case does not define required text."
        lowered = normalize(prediction)
        missing = [item for item in required if normalize(item) not in lowered]
        if not missing:
            return "pass", 1.0, "All required text is present."
        return "fail", 0.0, f"Missing required text: {', '.join(missing[:3])}."

    if kind == "not_contains":
        forbidden = _accepted_values(expected.get("value"))
        if not forbidden:
            return "fail", 0.0, "The case does not define forbidden text."
        lowered = normalize(prediction)
        present = [item for item in forbidden if normalize(item) in lowered]
        if not present:
            return "pass", 1.0, "No forbidden text is present."
        return "fail", 0.0, f"Forbidden text is present: {', '.join(present[:3])}."

    if kind == "regex":
        pattern = expected.get("value")
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX_CHARS:
            return "fail", 0.0, "The case does not define a usable regular expression."
        try:
            matched = re.search(pattern, prediction) is not None
        except re.error:
            return "fail", 0.0, "The expected regular expression is invalid."
        return ("pass", 1.0, "The response matches the expected pattern.") if matched else ("fail", 0.0, "The response does not match the expected pattern.")

    if kind == "json_schema":
        schema = expected.get("schema")
        if not isinstance(schema, dict) or not schema:
            return "fail", 0.0, "The case does not define a JSON Schema."
        try:
            parsed = extract_json(prediction)
        except ValueError:
            return "fail", 0.0, "The response does not contain a JSON value."
        errors = validate(parsed, schema, schema)
        if not errors:
            return "pass", 1.0, "The response is valid JSON that satisfies the schema."
        return "fail", 0.0, "The JSON does not satisfy the schema: " + "; ".join(errors[:3])

    if kind == "tool_call":
        tool = expected.get("tool")
        if not isinstance(tool, str) or not tool:
            return "fail", 0.0, "The case does not define an expected tool name."
        expected_arguments = expected.get("arguments")
        for call in tool_calls:
            if call.get("name") != tool:
                continue
            if expected_arguments is None:
                return "pass", 1.0, f"The response requested {tool}."
            problem = _arguments_subset(expected_arguments, call.get("arguments"))
            if problem is None:
                return "pass", 1.0, f"The response requested {tool} with the expected arguments."
        if not tool_calls:
            return "fail", 0.0, f"The response did not request {tool}."
        return "fail", 0.0, f"The response requested other tools or different arguments."

    if kind == "rubric":
        criteria = expected.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            return "fail", 0.0, "The rubric has no criteria."
        return "manual", None, "Manual rubric review required; this case is excluded from automatic accuracy."

    return "fail", 0.0, f"Unsupported expected type: {kind or 'missing'}."


def load_eval_cases(path: str | Path) -> tuple[list[EvalCase], list[str]]:
    cases: list[EvalCase] = []
    problems: list[str] = []
    seen: set[str] = set()
    target = Path(path)
    try:
        handle = target.open("r", encoding="utf-8")
    except OSError as error:
        return [], [f"Could not read {target}: {error}"]
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as error:
                problems.append(f"line {line_number}: invalid JSON ({error.msg})")
                continue
            if not isinstance(item, dict):
                problems.append(f"line {line_number}: expected an object")
                continue
            case_id = item.get("id")
            prompt = item.get("prompt")
            expected = item.get("expected")
            if not isinstance(case_id, str) or not case_id:
                problems.append(f"line {line_number}: id must be a non-empty string")
                continue
            if case_id in seen:
                problems.append(f"line {line_number}: duplicate id {case_id}")
                continue
            if not isinstance(prompt, str) or not prompt.strip():
                problems.append(f"line {line_number}: prompt must be a non-empty string")
                continue
            if not isinstance(expected, dict) or not expected.get("type"):
                problems.append(f"line {line_number}: expected must be an object with a type")
                continue
            seen.add(case_id)
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            max_tokens = item.get("max_tokens")
            cases.append(EvalCase(
                id=case_id,
                prompt=prompt,
                expected=expected,
                system=str(item.get("system") or ""),
                tags=[str(tag) for tag in tags if isinstance(tag, str)],
                max_tokens=max_tokens if isinstance(max_tokens, int) and 0 < max_tokens <= 8192 else None,
            ))
    return cases, problems


def load_predictions(path: str | Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    predictions: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    target = Path(path)
    try:
        handle = target.open("r", encoding="utf-8")
    except OSError as error:
        return {}, [f"Could not read {target}: {error}"]
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                problems.append(f"line {line_number}: invalid JSON")
                continue
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                problems.append(f"line {line_number}: prediction needs an id")
                continue
            predictions[item["id"]] = item
    return predictions, problems


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 1)


def evaluate(
    cases: list[EvalCase],
    predictions: dict[str, dict[str, Any]],
    *,
    model_label: str,
    source: str,
    dataset_path: str = "",
    extra_issues: Iterable[str] = (),
) -> dict[str, Any]:
    results: list[EvalResult] = []
    latencies: list[float] = []

    for case in cases:
        item = predictions.get(case.id)
        if item is None:
            results.append(EvalResult(case, "fail", 0.0, "No prediction was supplied for this case.", error="missing_prediction"))
            continue
        if item.get("error"):
            results.append(EvalResult(case, "fail", 0.0, "The backend returned an error for this case.", error=str(item["error"])[:500]))
            continue
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        tool_calls = item.get("tool_calls") if isinstance(item.get("tool_calls"), list) else []
        tool_calls = [call for call in tool_calls if isinstance(call, dict)]
        latency = item.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            latencies.append(float(latency))
        status, score, reason = score_case(case, text, tool_calls)
        results.append(EvalResult(
            case=case,
            status=status,
            score=score,
            reason=reason,
            prediction=text,
            tool_calls=tool_calls,
            latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
        ))

    automated = [result for result in results if result.status != "manual"]
    passed = [result for result in automated if result.status == "pass"]
    manual = [result for result in results if result.status == "manual"]
    errors = [result for result in results if result.error]

    def group(key: str) -> dict[str, dict[str, Any]]:
        bucket: dict[str, dict[str, Any]] = {}
        for result in automated:
            keys = result.case.tags if key == "tag" else [str(result.case.expected.get("type", "unknown"))]
            for item in keys:
                entry = bucket.setdefault(item, {"total": 0, "passed": 0})
                entry["total"] += 1
                entry["passed"] += 1 if result.status == "pass" else 0
        for entry in bucket.values():
            entry["accuracy"] = round(entry["passed"] / entry["total"], 4) if entry["total"] else None
        return dict(sorted(bucket.items()))

    report = {
        "schema": "vsutra-eval-report-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model_label,
        "dataset": dataset_path,
        "source": source,
        "summary": {
            "cases": len(cases),
            "automated": len(automated),
            "passed": len(passed),
            "failed": len(automated) - len(passed),
            "manual_pending": len(manual),
            "backend_errors": len(errors),
            "accuracy": round(len(passed) / len(automated), 4) if automated else None,
        },
        "latency_ms": {
            "count": len(latencies),
            "median": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "by_type": group("type"),
        "by_tag": group("tag"),
        "issues": list(extra_issues),
        "results": [result.as_dict() for result in results],
    }
    return report


async def evaluate_with_backend_async(
    cases: list[EvalCase],
    backend: Any,
    *,
    model_label: str,
    dataset_path: str = "",
    temperature: float = 0.0,
    max_tokens: int = 512,
    seed: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    predictions: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for case in cases:
        messages = [{"role": "system", "content": case.system}] if case.system else []
        messages.append({"role": "user", "content": case.prompt})
        try:
            result: ChatResult = await backend.generate(
                messages,
                temperature=temperature,
                max_tokens=case.max_tokens or max_tokens,
                seed=seed,
            )
        except InferenceError as error:
            predictions[case.id] = {"id": case.id, "error": str(error)}
            continue
        except Exception:
            predictions[case.id] = {"id": case.id, "error": "Unexpected inference failure."}
            continue
        predictions[case.id] = {
            "id": case.id,
            "text": result.text,
            "tool_calls": result.tool_calls,
            "latency_ms": result.latency_ms,
        }
    report = evaluate(cases, predictions, model_label=model_label, source="live backend", dataset_path=dataset_path, extra_issues=issues)
    return report, issues


def evaluate_with_backend(
    cases: list[EvalCase],
    backend: Any,
    *,
    model_label: str,
    dataset_path: str = "",
    temperature: float = 0.0,
    max_tokens: int = 512,
    seed: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    return asyncio.run(evaluate_with_backend_async(
        cases,
        backend,
        model_label=model_label,
        dataset_path=dataset_path,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
    ))


def write_report(report: dict[str, Any], output_dir: str | Path, stem: str = "eval") -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{stem}.json"
    markdown_path = target / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Evaluation report",
        "",
        f"- Model: `{report['model']}`",
        f"- Dataset: `{report['dataset'] or 'not recorded'}`",
        f"- Source: {report['source']}",
        f"- Created: {report['created_at']}",
        f"- Cases: {summary['cases']} · automated {summary['automated']} · passed {summary['passed']} · failed {summary['failed']} · manual pending {summary['manual_pending']}",
        f"- Automatic accuracy: {summary['accuracy'] if summary['accuracy'] is not None else 'not available'}",
        "",
    ]
    latency = report.get("latency_ms", {})
    if latency.get("count"):
        lines.extend([
            "## Latency",
            "",
            f"- Median: {latency['median']} ms",
            f"- p95: {latency['p95']} ms",
            f"- Max: {latency['max']} ms",
            "",
        ])
    lines.extend([
        "## Accuracy by type",
        "",
        "| Type | Passed | Total | Accuracy |",
        "| --- | ---: | ---: | ---: |",
    ])
    for key, value in report.get("by_type", {}).items():
        lines.append(f"| {key} | {value['passed']} | {value['total']} | {value['accuracy']} |")
    if report.get("by_tag"):
        lines.extend(["", "## Accuracy by tag", "", "| Tag | Passed | Total | Accuracy |", "| --- | ---: | ---: | ---: |"])
        for key, value in report["by_tag"].items():
            lines.append(f"| {key} | {value['passed']} | {value['total']} | {value['accuracy']} |")
    failures = [item for item in report.get("results", []) if item["status"] == "fail"]
    if failures:
        lines.extend(["", "## Failures", ""])
        for item in failures[:30]:
            lines.append(f"- **{item['id']}** ({item['expected_type']}): {item['reason']}")
            if item.get("error"):
                lines.append(f"  - Error: {item['error']}")
    manual = [item for item in report.get("results", []) if item["status"] == "manual"]
    if manual:
        lines.extend(["", "## Manual review pending", ""])
        lines.extend(f"- {item['id']}: {item['reason']}" for item in manual)
    if report.get("issues"):
        lines.extend(["", "## Input issues", ""])
        lines.extend(f"- {item}" for item in report["issues"])
    lines.extend([
        "",
        "---",
        "",
        "This report contains only responses and measurements produced during this run. "
        "Manual rubric cases are not scored automatically.",
        "",
    ])
    return "\n".join(lines)
