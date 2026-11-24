"""Model registry manifests, lineage, and evidence-gated promotion.

The registry never stores model weights. It records what produced a model, which dataset
and configuration were used, what evidence exists, and which lifecycle stage the model may
claim. Promotion is blocked when the required evaluation or benchmark evidence is missing,
so a model cannot be labeled production on the strength of a template alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
STAGES = ("draft", "evaluated", "canary", "production", "retired")
ALLOWED_TRANSITIONS = {
    "draft": {"evaluated", "retired"},
    "evaluated": {"canary", "retired"},
    "canary": {"production", "retired"},
    "production": {"retired"},
    "retired": set(),
}
REQUIRED_FIELDS = ("schema", "name", "version", "base_model", "method", "dataset", "config", "stage", "created_at")


class RegistryError(ValueError):
    """A registry operation cannot proceed safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve(path_value: str, workspace: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else workspace / path


def load_manifest(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except OSError as error:
        raise RegistryError(f"Could not read {target}: {error}") from error
    except json.JSONDecodeError as error:
        raise RegistryError(f"{target} is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise RegistryError(f"{target} must contain a JSON object.")
    return parsed


def _write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def manifest_path(root: str | Path, name: str, version: str) -> Path:
    return Path(root) / name / f"{version}.json"


def create_manifest(
    root: str | Path,
    *,
    name: str,
    version: str,
    base_model: str,
    method: str,
    dataset_path: str | Path,
    config_path: str | Path,
    workspace: str | Path | None = None,
    base_revision: str = "",
    notes: str = "",
) -> Path:
    if not NAME_RE.match(name):
        raise RegistryError("name must be lowercase letters, digits, dots, dashes, or underscores (3-64 characters).")
    if not VERSION_RE.match(version):
        raise RegistryError("version must be semantic versioning, for example 0.1.0 or 0.2.0-rc.1.")
    if method not in {"full", "lora", "qlora"}:
        raise RegistryError("method must be full, lora, or qlora.")
    dataset = Path(dataset_path)
    config = Path(config_path)
    if not dataset.exists():
        raise RegistryError(f"Dataset does not exist: {dataset}")
    if not config.exists():
        raise RegistryError(f"Config does not exist: {config}")
    workspace_path = Path(workspace) if workspace is not None else Path(root).parent
    target = manifest_path(root, name, version)
    if target.exists():
        raise RegistryError(f"Manifest already exists: {target}")

    manifest = {
        "schema": "vsutra-model-manifest-v1",
        "name": name,
        "version": version,
        "stage": "draft",
        "created_at": _now(),
        "updated_at": _now(),
        "base_model": base_model,
        "base_revision": base_revision,
        "method": method,
        "dataset": {
            "path": _relative(dataset, workspace_path),
            "sha256": sha256_file(dataset),
            "examples": _count_jsonl(dataset),
        },
        "config": {
            "path": _relative(config, workspace_path),
            "sha256": sha256_file(config),
        },
        "evaluation": None,
        "benchmark": None,
        "artifacts": [],
        "lineage": {"parent_version": None},
        "history": [{"at": _now(), "stage": "draft", "actor": _actor(), "note": notes or "Manifest created; no training has run."}],
        "notes": notes,
    }
    return _write_manifest(target, manifest)


def _actor() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "local-operator"


def _count_jsonl(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def verify_manifest(path: str | Path, workspace: str | Path | None = None) -> list[str]:
    manifest = load_manifest(path)
    workspace_path = Path(workspace) if workspace is not None else Path(path).resolve().parent.parent
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if not manifest.get(field):
            errors.append(f"Missing required field: {field}")
    if manifest.get("schema") != "vsutra-model-manifest-v1":
        errors.append("schema must be vsutra-model-manifest-v1")
    if not NAME_RE.match(str(manifest.get("name", ""))):
        errors.append("name is not valid")
    if not VERSION_RE.match(str(manifest.get("version", ""))):
        errors.append("version is not semantic versioning")
    if manifest.get("method") not in {"full", "lora", "qlora"}:
        errors.append("method must be full, lora, or qlora")
    if manifest.get("stage") not in STAGES:
        errors.append("stage is not recognized")
    for section in ("dataset", "config"):
        record = manifest.get(section)
        if not isinstance(record, dict):
            errors.append(f"{section} must be an object")
            continue
        relative = record.get("path")
        expected_hash = record.get("sha256")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{section}.path is required")
            continue
        resolved = _resolve(relative, workspace_path)
        if not resolved.exists():
            errors.append(f"{section} path does not exist: {relative}")
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            errors.append(f"{section}.sha256 is missing or malformed")
        else:
            actual = sha256_file(resolved)
            if actual != expected_hash:
                errors.append(f"{section} hash changed: expected {expected_hash[:12]}…, found {actual[:12]}…")
    evaluation = manifest.get("evaluation")
    if evaluation is not None:
        if not isinstance(evaluation, dict) or not isinstance(evaluation.get("accuracy"), (int, float)):
            errors.append("evaluation must include a numeric accuracy")
    benchmark = manifest.get("benchmark")
    if benchmark is not None:
        if not isinstance(benchmark, dict) or not isinstance(benchmark.get("successes"), int) or benchmark["successes"] < 1:
            errors.append("benchmark must include at least one successful request")
    if not isinstance(manifest.get("history"), list):
        errors.append("history must be an array")
    return errors


def attach_evaluation(
    path: str | Path,
    report_path: str | Path,
    *,
    workspace: str | Path | None = None,
) -> Path:
    manifest_file = Path(path)
    manifest = load_manifest(manifest_file)
    report_file = Path(report_path)
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"Could not read evaluation report: {error}") from error
    if report.get("schema") != "vsutra-eval-report-v1":
        raise RegistryError("Not a V-Sutra evaluation report.")
    summary = report.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("accuracy"), (int, float)):
        raise RegistryError("The evaluation report has no automatic accuracy score.")
    if not isinstance(summary.get("automated"), int) or summary["automated"] < 1:
        raise RegistryError("The evaluation report has no automated cases; it cannot support a promotion.")

    workspace_path = Path(workspace) if workspace is not None else manifest_file.resolve().parent.parent
    manifest["evaluation"] = {
        "report": _relative(report_file, workspace_path),
        "sha256": sha256_file(report_file),
        "accuracy": float(summary["accuracy"]),
        "automated": int(summary["automated"]),
        "model": report.get("model"),
        "created_at": report.get("created_at"),
    }
    manifest["updated_at"] = _now()
    manifest.setdefault("history", []).append({"at": _now(), "stage": manifest.get("stage"), "actor": _actor(), "note": "Evaluation report attached."})
    return _write_manifest(manifest_file, manifest)


def attach_benchmark(
    path: str | Path,
    report_path: str | Path,
    *,
    workspace: str | Path | None = None,
) -> Path:
    manifest_file = Path(path)
    manifest = load_manifest(manifest_file)
    report_file = Path(report_path)
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"Could not read benchmark report: {error}") from error
    if report.get("schema") != "vsutra-benchmark-report-v1":
        raise RegistryError("Not a V-Sutra benchmark report.")
    summary = report.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("successes"), int) or summary["successes"] < 1:
        raise RegistryError("The benchmark report has no successful requests.")

    workspace_path = Path(workspace) if workspace is not None else manifest_file.resolve().parent.parent
    manifest["benchmark"] = {
        "report": _relative(report_file, workspace_path),
        "sha256": sha256_file(report_file),
        "successes": int(summary["successes"]),
        "requests": int(summary.get("requests", 0)),
        "ttft_ms_p50": (summary.get("ttft_ms") or {}).get("median"),
        "latency_ms_p50": (summary.get("latency_ms") or {}).get("median"),
        "tokens_per_second_aggregate": (summary.get("tokens_per_second") or {}).get("aggregate"),
        "model": report.get("model"),
        "created_at": report.get("created_at"),
    }
    manifest["updated_at"] = _now()
    manifest.setdefault("history", []).append({"at": _now(), "stage": manifest.get("stage"), "actor": _actor(), "note": "Benchmark report attached."})
    return _write_manifest(manifest_file, manifest)


def promote(
    path: str | Path,
    stage: str,
    *,
    min_accuracy: float = 0.0,
    note: str = "",
) -> Path:
    manifest_file = Path(path)
    manifest = load_manifest(manifest_file)
    current = str(manifest.get("stage", "draft"))
    if stage not in STAGES:
        raise RegistryError(f"Unknown stage: {stage}")
    if stage not in ALLOWED_TRANSITIONS.get(current, set()):
        raise RegistryError(f"Cannot move from {current} to {stage}. Allowed: {', '.join(sorted(ALLOWED_TRANSITIONS.get(current, set()))) or 'none'}.")
    if stage in {"evaluated", "canary", "production"}:
        evaluation = manifest.get("evaluation")
        if not isinstance(evaluation, dict) or not isinstance(evaluation.get("accuracy"), (int, float)):
            raise RegistryError("Attach a real evaluation report before moving beyond draft.")
        if stage in {"canary", "production"} and evaluation["accuracy"] < min_accuracy:
            raise RegistryError(f"Evaluation accuracy {evaluation['accuracy']} is below the required {min_accuracy}.")
    if stage == "production":
        benchmark = manifest.get("benchmark")
        if not isinstance(benchmark, dict) or not isinstance(benchmark.get("successes"), int) or benchmark["successes"] < 1:
            raise RegistryError("Attach a benchmark report with successful requests before production promotion.")

    history = manifest.setdefault("history", [])
    if not isinstance(history, list):
        raise RegistryError("Manifest history is malformed.")
    history.append({"at": _now(), "stage": stage, "actor": _actor(), "note": note or f"Promoted from {current}."})
    manifest["stage"] = stage
    manifest["updated_at"] = _now()
    return _write_manifest(manifest_file, manifest)


def list_manifests(root: str | Path) -> list[dict[str, Any]]:
    base = Path(root)
    manifests: list[dict[str, Any]] = []
    if not base.exists():
        return manifests
    for path in sorted(base.glob("*/*.json")):
        if path.name.endswith(".example.json") or path.parts[-2].startswith("_"):
            continue
        try:
            manifest = load_manifest(path)
        except RegistryError:
            continue
        manifests.append({
            "path": str(path),
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "stage": manifest.get("stage"),
            "base_model": manifest.get("base_model"),
            "method": manifest.get("method"),
            "dataset_examples": (manifest.get("dataset") or {}).get("examples"),
            "accuracy": (manifest.get("evaluation") or {}).get("accuracy") if isinstance(manifest.get("evaluation"), dict) else None,
        })
    return manifests


def format_manifest(manifest: dict[str, Any]) -> str:
    evaluation = manifest.get("evaluation") if isinstance(manifest.get("evaluation"), dict) else {}
    benchmark = manifest.get("benchmark") if isinstance(manifest.get("benchmark"), dict) else {}
    lines = [
        f"{manifest.get('name')} {manifest.get('version')} · {manifest.get('stage')}",
        f"Base model: {manifest.get('base_model')} ({manifest.get('method')})",
        f"Dataset: {(manifest.get('dataset') or {}).get('path')} · {(manifest.get('dataset') or {}).get('examples')} examples",
        f"Config: {(manifest.get('config') or {}).get('path')}",
        f"Evaluation: {evaluation.get('accuracy', 'not attached')} · Benchmark successes: {benchmark.get('successes', 'not attached')}",
        f"Updated: {manifest.get('updated_at')}",
    ]
    return "\n".join(lines)
