"""Configuration loading and structural validation for research templates.

Templates are intentionally framework-neutral. They describe the experiment, data,
adapter, training budget, evaluation, and planned hardware; they are not executed by this
workspace and do not start training.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TRAINING_METHODS = {"full", "lora", "qlora"}
BACKEND_KINDS = {"openai", "ollama", "vsutra", "none"}


class ConfigError(ValueError):
    """A configuration cannot be used for planning."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - exercised only in bare environments
        raise ConfigError("PyYAML is required to read research templates: pip install PyYAML") from error
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"Could not read {target}: {error}") from error
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ConfigError(f"{target} is not valid YAML: {error}") from error
    if not isinstance(parsed, dict):
        raise ConfigError(f"{target} must contain a YAML mapping.")
    return parsed


def _require(config: dict[str, Any], key: str, errors: list[str]) -> Any:
    value = config.get(key)
    if value in (None, "", [], {}):
        errors.append(f"Missing required field: {key}")
    return value


def validate_training_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("run_name", "base_model", "method", "hardware", "training", "data", "output"):
        _require(config, key, errors)

    method = str(config.get("method", "")).lower()
    if method and method not in TRAINING_METHODS:
        errors.append("method must be full, lora, or qlora")

    hardware = config.get("hardware")
    if isinstance(hardware, dict):
        for key in ("target_gpu", "gpu_count", "system_ram_gb", "storage_gb"):
            if hardware.get(key) in (None, ""):
                errors.append(f"Missing hardware field: hardware.{key}")
        if not isinstance(hardware.get("measured"), bool):
            errors.append("hardware.measured must be true or false; record measured peak memory after a smoke run.")
        if hardware.get("measured") is False:
            warnings.append("Hardware is a plan, not a measured result. Do not start a full run before measuring peak memory.")
        for key in ("gpu_count", "system_ram_gb", "storage_gb"):
            value = hardware.get(key)
            if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                errors.append(f"hardware.{key} must be a positive number")
    elif hardware is not None:
        errors.append("hardware must be a mapping")

    training = config.get("training")
    if isinstance(training, dict):
        for key in ("epochs", "learning_rate", "batch_size", "gradient_accumulation", "max_seq_len", "seed"):
            value = training.get(key)
            if value is None:
                errors.append(f"Missing training field: training.{key}")
            elif not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"training.{key} must be a positive number")
        if training.get("gradient_checkpointing") not in (True, False):
            errors.append("training.gradient_checkpointing must be true or false")
    elif training is not None:
        errors.append("training must be a mapping")

    data = config.get("data")
    if isinstance(data, dict):
        if not isinstance(data.get("train"), str) or not data.get("train"):
            errors.append("data.train must be a JSONL path")
        if data.get("format") != "vsutra-instruction-v1":
            errors.append("data.format must be vsutra-instruction-v1")
        base = Path(".")  # only used for warning text; paths may be relative to the workspace root
        for split in ("train", "validation"):
            value = data.get(split)
            if isinstance(value, str) and not (base / value).exists():
                warnings.append(f"data.{split} does not exist yet: {value}")
    elif data is not None:
        errors.append("data must be a mapping")

    if method in {"lora", "qlora"}:
        lora = config.get("lora")
        if not isinstance(lora, dict):
            errors.append("lora settings are required for LoRA and QLoRA runs")
        else:
            rank = lora.get("rank")
            alpha = lora.get("alpha")
            if not isinstance(rank, int) or rank <= 0:
                errors.append("lora.rank must be a positive integer")
            if not isinstance(alpha, (int, float)) or alpha <= 0:
                errors.append("lora.alpha must be a positive number")
            if isinstance(rank, int) and isinstance(alpha, (int, float)) and rank > 0:
                ratio = alpha / rank
                if ratio < 0.5 or ratio > 4:
                    warnings.append(f"lora.alpha/rank ratio is {ratio:g}; common ranges are roughly 0.5-4.")
            modules = lora.get("target_modules")
            if not isinstance(modules, list) or not modules:
                errors.append("lora.target_modules must be a non-empty list")
    if method == "qlora":
        quantization = config.get("quantization")
        if not isinstance(quantization, dict):
            errors.append("quantization settings are required for QLoRA runs")
        else:
            if str(quantization.get("type", "")).lower() not in {"nf4", "4bit", "int4"}:
                errors.append("QLoRA requires quantization.type of nf4 or 4bit")
            if str(quantization.get("compute_dtype", "")).lower() not in {"bf16", "fp16"}:
                errors.append("quantization.compute_dtype must be bf16 or fp16")

    evaluation = config.get("evaluation")
    if evaluation is not None and not isinstance(evaluation, dict):
        errors.append("evaluation must be a mapping when present")

    output = config.get("output")
    if isinstance(output, dict) and not isinstance(output.get("dir"), str):
        errors.append("output.dir must be a directory path")

    return errors, warnings


def validate_eval_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config.get("dataset"), str) or not config.get("dataset"):
        errors.append("dataset must be a JSONL evaluation path")
    backend = config.get("backend")
    if not isinstance(backend, dict):
        errors.append("backend must be a mapping")
    else:
        kind = str(backend.get("kind", "")).lower()
        if kind not in BACKEND_KINDS:
            errors.append("backend.kind must be openai, ollama, vsutra, or none")
        if kind != "none":
            if not isinstance(backend.get("base_url"), str):
                errors.append("backend.base_url is required unless backend.kind is none")
            if not isinstance(backend.get("model"), str):
                errors.append("backend.model is required unless backend.kind is none")
        temperature = backend.get("temperature", 0)
        if not isinstance(temperature, (int, float)) or temperature < 0:
            errors.append("backend.temperature must be zero or positive")
        elif temperature > 0:
            warnings.append("Non-zero temperature makes deterministic evaluation harder; record the seed and repeat runs.")
    if not isinstance(config.get("output_dir"), str):
        warnings.append("output_dir is not set; reports will be written next to the dataset.")
    return errors, warnings


def validate_benchmark_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config.get("prompts"), str) or not config.get("prompts"):
        errors.append("prompts must be a JSONL path")
    backend = config.get("backend")
    if not isinstance(backend, dict):
        errors.append("backend must be a mapping")
    else:
        kind = str(backend.get("kind", "")).lower()
        if kind not in BACKEND_KINDS - {"none"}:
            errors.append("benchmark backend.kind must be openai, ollama, or vsutra")
        if not isinstance(backend.get("base_url"), str):
            errors.append("backend.base_url is required")
        if not isinstance(backend.get("model"), str):
            errors.append("backend.model is required")
    for key in ("repeats", "warmup"):
        value = config.get(key, 0)
        if not isinstance(value, int) or value < 0:
            errors.append(f"{key} must be a non-negative integer")
    if isinstance(config.get("repeats"), int) and config.get("repeats", 0) < 1:
        errors.append("repeats must be at least 1 for a benchmark")
    if isinstance(config.get("warmup"), int) and config.get("warmup", 0) < 1:
        warnings.append("No warmup request configured; first-request latency will include model load.")
    return errors, warnings


def format_config_check(name: str, errors: list[str], warnings: list[str]) -> str:
    lines = [f"Configuration: {name}"]
    if errors:
        lines.append(f"Errors ({len(errors)}):")
        lines.extend(f"  - {item}" for item in errors)
    if warnings:
        lines.append(f"Warnings ({len(warnings)}):")
        lines.extend(f"  - {item}" for item in warnings)
    lines.append("PASS" if not errors else "FAIL")
    return "\n".join(lines)
