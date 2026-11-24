"""Benchmark harness for real inference endpoints.

The harness measures only what an endpoint returns during this run: time to first token,
total latency, reported or whitespace-estimated output length, and failures. If every
request fails, it reports the failures and no performance numbers.

It does not start a server, load a model, or reuse another run's numbers.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .inference import ChatResult, InferenceError, build_backend


def load_prompts(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    prompts: list[dict[str, str]] = []
    problems: list[str] = []
    try:
        handle = Path(path).open("r", encoding="utf-8")
    except OSError as error:
        return [], [f"Could not read {path}: {error}"]
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                problems.append(f"line {line_number}: invalid JSON")
                continue
            if not isinstance(item, dict) or not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
                problems.append(f"line {line_number}: prompt must be a non-empty string")
                continue
            system = item.get("system") if isinstance(item.get("system"), str) else ""
            prompts.append({
                "id": str(item.get("id") or f"prompt-{len(prompts) + 1}"),
                "prompt": item["prompt"],
                "system": system,
            })
    return prompts, problems


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 1)


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total_physical / (1024 ** 3), 1)
        else:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round(pages * page_size / (1024 ** 3), 1)
    except (AttributeError, ValueError, OSError):
        return None
    return None


def _gpu_names() -> list[str]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return []
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()][:8]
    except (OSError, subprocess.SubprocessError):
        return []


def environment_info() -> dict[str, Any]:
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "system_ram_gb": _ram_gb(),
        "gpus": _gpu_names(),
        "note": "GPU and RAM values are read from the host; null means unavailable.",
    }


async def run_benchmark_async(
    prompts: list[dict[str, str]],
    backend: Any,
    *,
    repeats: int = 3,
    warmup: int = 1,
    max_tokens: int = 256,
    temperature: float = 0.0,
    seed: int | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for index in range(max(0, warmup)):
        prompt = prompts[index % len(prompts)]
        messages = [{"role": "system", "content": prompt["system"]}] if prompt["system"] else []
        messages.append({"role": "user", "content": prompt["prompt"]})
        try:
            await backend.generate(messages, temperature=temperature, max_tokens=max_tokens, seed=seed)
        except InferenceError:
            # Warmup failures are not results; the measured pass will record its own failures.
            pass

    for repeat in range(max(1, repeats)):
        for prompt in prompts:
            messages = [{"role": "system", "content": prompt["system"]}] if prompt["system"] else []
            messages.append({"role": "user", "content": prompt["prompt"]})
            record: dict[str, Any] = {
                "id": prompt["id"],
                "repeat": repeat + 1,
                "ok": False,
                "error": None,
                "ttft_ms": None,
                "latency_ms": None,
                "output_chars": 0,
                "output_tokens": 0,
                "token_count_source": "unavailable",
                "tokens_per_second": None,
            }
            try:
                result: ChatResult = await backend.generate(
                    messages, temperature=temperature, max_tokens=max_tokens, seed=seed
                )
            except InferenceError as error:
                record["error"] = str(error)
                records.append(record)
                continue
            except Exception:
                record["error"] = "Unexpected inference failure."
                records.append(record)
                continue

            record["ok"] = True
            record["ttft_ms"] = round(result.ttft_ms, 1) if result.ttft_ms is not None else None
            record["latency_ms"] = round(result.latency_ms, 1)
            record["output_chars"] = len(result.text)
            record["output_tokens"] = result.approx_output_tokens
            record["token_count_source"] = "reported" if isinstance(result.usage.get("completion_tokens"), int) else "estimated"
            generation_ms = (result.latency_ms - (result.ttft_ms or 0))
            if generation_ms > 0 and record["output_tokens"] > 1:
                record["tokens_per_second"] = round((record["output_tokens"] - 1) / (generation_ms / 1000), 2)
            records.append(record)

    return summarize(records, backend.name, repeats=repeats, warmup=warmup)


def summarize(records: list[dict[str, Any]], model_label: str, *, repeats: int, warmup: int) -> dict[str, Any]:
    successes = [record for record in records if record.get("ok")]
    failures = [record for record in records if not record.get("ok")]
    ttft = [record["ttft_ms"] for record in successes if isinstance(record.get("ttft_ms"), (int, float))]
    latency = [record["latency_ms"] for record in successes if isinstance(record.get("latency_ms"), (int, float))]
    throughputs = [record["tokens_per_second"] for record in successes if isinstance(record.get("tokens_per_second"), (int, float))]
    total_tokens = sum(int(record.get("output_tokens", 0)) for record in successes)
    total_generation_seconds = sum(
        max(0.0, (record.get("latency_ms") or 0) - (record.get("ttft_ms") or 0)) / 1000 for record in successes
    )
    summary: dict[str, Any] = {
        "requests": len(records),
        "successes": len(successes),
        "failures": len(failures),
        "success_rate": round(len(successes) / len(records), 4) if records else None,
        "ttft_ms": {"count": len(ttft), "median": _percentile(ttft, 0.5), "p95": _percentile(ttft, 0.95), "max": round(max(ttft), 1) if ttft else None},
        "latency_ms": {"count": len(latency), "median": _percentile(latency, 0.5), "p95": _percentile(latency, 0.95), "max": round(max(latency), 1) if latency else None},
        "tokens_per_second": {
            "median": _percentile(throughputs, 0.5),
            "p95": _percentile(throughputs, 0.95),
            "aggregate": round(total_tokens / total_generation_seconds, 2) if total_generation_seconds > 0 else None,
        },
        "token_count_source": sorted({record.get("token_count_source") for record in successes if record.get("token_count_source")}),
    }
    return {
        "schema": "vsutra-benchmark-report-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model_label,
        "environment": environment_info(),
        "config": {"repeats": repeats, "warmup": warmup, "prompts": len({record.get("id") for record in records})},
        "summary": summary if successes else None,
        "records": records,
        "failures": [
            {"id": record.get("id"), "repeat": record.get("repeat"), "error": record.get("error")}
            for record in failures[:50]
        ],
        "notes": [
            "Only successful requests contribute to latency and throughput statistics.",
            "Token counts are taken from provider usage when reported; otherwise they are whitespace estimates.",
            "Results are valid only for the recorded environment, server build, model, and request settings.",
        ] if successes else [
            "No requests succeeded, so no performance statistics are reported.",
            "Check that the server is running, the model name is served, and authentication is configured on the backend host.",
        ],
    }


def write_report(report: dict[str, Any], output_dir: str | Path, stem: str = "benchmark") -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{stem}.json"
    markdown_path = target / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def render_markdown(report: dict[str, Any]) -> str:
    environment = report["environment"]
    lines = [
        "# Benchmark report",
        "",
        f"- Model: `{report['model']}`",
        f"- Measured: {environment['measured_at']}",
        f"- Environment: {environment['platform']} · Python {environment['python']} · {environment['cpu_count']} CPUs · {environment['system_ram_gb'] or 'unknown'} GB RAM",
        f"- GPUs: {', '.join(environment['gpus']) if environment['gpus'] else 'not detected'}",
        f"- Requests: {report['config']['repeats']} repeats × {report['config']['prompts']} prompts · {report['config']['warmup']} warmup",
        "",
    ]
    summary = report.get("summary")
    if not summary:
        lines.extend(["## Result", "", "No requests succeeded. No performance statistics are reported.", ""])
        if report.get("failures"):
            lines.extend(["Failures:", ""])
            lines.extend(f"- {item['id']} (repeat {item['repeat']}): {item['error']}" for item in report["failures"])
        return "\n".join(lines) + "\n"

    lines.extend([
        "## Results",
        "",
        f"- Success rate: {summary['successes']}/{summary['requests']} ({summary['success_rate']})",
        f"- Time to first token: median {summary['ttft_ms']['median']} ms · p95 {summary['ttft_ms']['p95']} ms",
        f"- Total latency: median {summary['latency_ms']['median']} ms · p95 {summary['latency_ms']['p95']} ms",
        f"- Tokens/second: median {summary['tokens_per_second']['median']} · p95 {summary['tokens_per_second']['p95']} · aggregate {summary['tokens_per_second']['aggregate']}",
        f"- Token counts: {', '.join(summary['token_count_source'])}",
        "",
    ])
    if report.get("failures"):
        lines.extend(["## Failures", ""])
        lines.extend(f"- {item['id']} (repeat {item['repeat']}): {item['error']}" for item in report["failures"])
        lines.append("")
    lines.extend(["---", "", *[f"- {note}" for note in report.get("notes", [])], ""])
    return "\n".join(lines)
