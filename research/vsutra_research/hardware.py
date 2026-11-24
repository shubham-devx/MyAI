"""Planning-grade GPU VRAM, system RAM, and storage estimates.

Every number is derived from the formulas documented in ``docs/hardware-requirements.md``.
They are estimates for planning and comparison only; no training recommendation is valid
until the target machine's measured memory usage is recorded. The workspace never reports
a measured number that it did not measure.

Formula summary
---------------
- Weights: parameter count x bytes per parameter for the chosen base precision.
- Trainable parameters: all parameters for full fine-tuning, or ``layers x modules x 2 x
  rank x hidden`` for LoRA/QLoRA.
- Gradients: trainable parameters x gradient dtype bytes.
- Optimizer: trainable parameters x (12 bytes AdamW FP32, 6 bytes 8-bit AdamW, 4 bytes SGD
  momentum, 0 bytes none).
- Activations: ``batch x sequence x hidden x layers x dtype x factor`` plus logits.
- QLoRA base weights: 0.55 bytes per parameter to account for NF4 values, quantization
  constants, and double quantization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

GB = 1_000_000_000
DTYPE_BYTES = {"fp32": 4.0, "fp16": 2.0, "bf16": 2.0, "int8": 1.0, "int4": 0.5, "nf4": 0.55}
OPTIMIZER_BYTES = {"adamw": 12.0, "adamw_8bit": 6.0, "sgd": 4.0, "none": 0.0}
GPU_SIZES = (8, 12, 16, 24, 40, 48, 80)


@dataclass(frozen=True)
class ModelProfile:
    name: str
    params: int
    layers: int
    hidden: int
    heads: int
    kv_heads: int
    vocab: int
    exact: bool = True

    @property
    def head_dim(self) -> int:
        return max(1, self.hidden // max(1, self.heads))


PROFILES: dict[str, ModelProfile] = {
    "0.5": ModelProfile("0.5B class", 494_000_000, 24, 896, 14, 2, 151_936),
    "1.5": ModelProfile("1.5B class", 1_543_000_000, 28, 1536, 12, 2, 151_936),
    "3": ModelProfile("3B class", 3_210_000_000, 28, 3072, 24, 8, 128_256),
    "7": ModelProfile("7B class", 6_740_000_000, 32, 4096, 32, 32, 32_000),
    "8": ModelProfile("8B class", 8_030_000_000, 32, 4096, 32, 8, 128_256),
    "13": ModelProfile("13B class", 13_020_000_000, 40, 5120, 40, 40, 32_000),
    "14": ModelProfile("14B class", 14_700_000_000, 48, 5120, 40, 8, 152_064),
    "32": ModelProfile("32B class", 32_500_000_000, 64, 5120, 40, 8, 152_064),
    "70": ModelProfile("70B class", 70_600_000_000, 80, 8192, 64, 8, 128_256),
}


def _synthetic_profile(params_b: float) -> ModelProfile:
    params = max(1, int(params_b * 1_000_000_000))
    layers = max(12, round(8 + 12 * (params_b ** (1 / 3))))
    hidden = 128 * max(8, round(math.sqrt(params / (layers * 12)) / 128))
    heads = max(8, hidden // 128)
    return ModelProfile(
        name=f"{params_b:g}B synthetic profile",
        params=params,
        layers=layers,
        hidden=hidden,
        heads=heads,
        kv_heads=max(1, heads // 4),
        vocab=128_256,
        exact=False,
    )


def profile_for(params_b: float) -> ModelProfile:
    key = f"{params_b:g}"
    if key in PROFILES:
        return PROFILES[key]
    nearest = min(PROFILES.items(), key=lambda item: abs(int(item[0].split(".")[0]) - params_b))
    if abs(nearest[1].params / 1_000_000_000 - params_b) / params_b <= 0.03:
        return nearest[1]
    return _synthetic_profile(params_b)


def _round_gb(value: float) -> float:
    return round(value, 2)


def _gpu_plan(required_gb: float) -> dict[str, Any]:
    for size in GPU_SIZES:
        if required_gb <= size:
            return {"gpus": 1, "gpu_gb": size, "label": f"1 x {size} GB", "fits_single_gpu": True}
    count = math.ceil(required_gb / 80)
    return {"gpus": count, "gpu_gb": 80, "label": f"{count} x 80 GB", "fits_single_gpu": False}


def _ram_floor(params_b: float) -> int:
    if params_b <= 2:
        return 16
    if params_b <= 10:
        return 32
    if params_b <= 40:
        return 64
    return 128


def estimate_training(
    params_b: float,
    *,
    method: str = "qlora",
    precision: str = "bf16",
    seq_len: int = 2048,
    batch_size: int = 1,
    gradient_accumulation: int = 8,
    checkpointing: bool = True,
    lora_rank: int = 16,
    lora_modules: int = 4,
    optimizer: str | None = None,
    offload_optimizer: bool = False,
    save_limit: int = 2,
    dataset_gb: float = 1.0,
) -> dict[str, Any]:
    method = method.lower()
    if method not in {"full", "lora", "qlora"}:
        raise ValueError("method must be full, lora, or qlora")
    precision = precision.lower()
    if precision not in {"fp32", "fp16", "bf16", "int8", "int4", "nf4"}:
        raise ValueError("unsupported precision")
    if method == "qlora":
        precision = "nf4"
    if method != "full" and precision not in {"fp16", "bf16"}:
        precision = "bf16"
    if seq_len < 128 or seq_len > 1_000_000:
        raise ValueError("seq_len must be between 128 and 1,000,000")
    if batch_size < 1 or batch_size > 4096:
        raise ValueError("batch_size must be between 1 and 4096")
    if lora_rank < 1 or lora_modules < 1:
        raise ValueError("LoRA rank and module count must be positive")
    optimizer = (optimizer or ("adamw_8bit" if method == "qlora" else "adamw")).lower()
    if optimizer not in OPTIMIZER_BYTES:
        raise ValueError("unsupported optimizer")

    profile = profile_for(params_b)
    params = profile.params
    train_bytes = DTYPE_BYTES["fp32"] if precision == "fp32" else 2.0
    base_bpp = DTYPE_BYTES[precision] if method in {"full", "lora"} else DTYPE_BYTES["nf4"]

    if method == "full":
        trainable = params
    else:
        trainable = profile.layers * lora_modules * 2 * lora_rank * profile.hidden
    lora_fraction = trainable / params if params else 0.0

    weights_gb = params * base_bpp / GB
    gradients_gb = trainable * train_bytes / GB
    optimizer_gb = trainable * OPTIMIZER_BYTES[optimizer] / GB
    activation_factor = 4.0 if checkpointing else 18.0
    activations_gb = batch_size * seq_len * profile.hidden * profile.layers * train_bytes * activation_factor / GB
    logits_gb = batch_size * seq_len * profile.vocab * train_bytes / GB
    subtotal = weights_gb + gradients_gb + optimizer_gb + activations_gb + logits_gb
    overhead_gb = 1.0 + subtotal * 0.05
    total_gb = subtotal + overhead_gb
    recommended_vram = total_gb * 1.15
    plan = _gpu_plan(recommended_vram)

    ram_formula = weights_gb * 1.1 + 8.0 + (optimizer_gb if offload_optimizer else 0.0)
    system_ram_gb = max(_ram_floor(params_b), math.ceil(ram_formula))
    if not plan["fits_single_gpu"]:
        system_ram_gb = max(system_ram_gb, math.ceil(ram_formula + 32))

    source_weights_gb = params * 2 / GB
    if method == "full":
        checkpoint_gb = weights_gb
    else:
        checkpoint_gb = max(0.02, trainable * 6 / GB)
    storage_gb = math.ceil(source_weights_gb + checkpoint_gb * max(1, save_limit) + max(0.0, dataset_gb) + 10)

    notes = [
        "Planning-grade estimate (roughly +/-30%); measure real peak memory before committing to hardware.",
        "Activations assume the stated micro-batch and sequence length with gradient checkpointing "
        + ("enabled." if checkpointing else "disabled."),
        f"Gradient accumulation ({gradient_accumulation}) does not change per-step activation memory.",
    ]
    if method == "qlora":
        notes.append("QLoRA stores frozen base weights in NF4 with double quantization and computes in bf16.")
    if method == "lora":
        notes.append("LoRA keeps frozen base weights in the training precision and trains only adapters.")
    if method == "full":
        notes.append("Full fine-tuning updates every parameter and stores full optimizer state and full checkpoints.")
    if offload_optimizer:
        notes.append("Optimizer offload increases host RAM traffic and lowers throughput.")
    if not profile.exact:
        notes.append(f"Layer/hidden dimensions for {params_b:g}B were approximated; prefer an exact model profile for final sizing.")
    if not plan["fits_single_gpu"]:
        notes.append("This plan needs multiple GPUs; tensor/pipeline parallelism adds communication overhead not modeled here.")

    return {
        "kind": "training",
        "profile": {
            "name": profile.name,
            "params": params,
            "layers": profile.layers,
            "hidden": profile.hidden,
            "exact": profile.exact,
        },
        "inputs": {
            "method": method,
            "precision": precision,
            "seq_len": seq_len,
            "batch_size": batch_size,
            "gradient_accumulation": gradient_accumulation,
            "checkpointing": checkpointing,
            "lora_rank": lora_rank if method != "full" else 0,
            "lora_modules": lora_modules if method != "full" else 0,
            "optimizer": optimizer,
            "offload_optimizer": offload_optimizer,
        },
        "breakdown_gb": {
            "base_weights": _round_gb(weights_gb),
            "gradients": _round_gb(gradients_gb),
            "optimizer_state": _round_gb(optimizer_gb),
            "activations": _round_gb(activations_gb),
            "logits": _round_gb(logits_gb),
            "overhead": _round_gb(overhead_gb),
            "total": _round_gb(total_gb),
        },
        "trainable_params": trainable,
        "trainable_fraction": round(lora_fraction, 6),
        "vram_recommended_gb": _round_gb(recommended_vram),
        "gpu_plan": plan,
        "system_ram_gb": system_ram_gb,
        "storage_gb": storage_gb,
        "notes": notes,
    }


def estimate_inference(
    params_b: float,
    *,
    precision: str = "int4",
    context: int = 8192,
    concurrency: int = 1,
    kv_bytes: float = 2.0,
) -> dict[str, Any]:
    profile = profile_for(params_b)
    precision = precision.lower()
    if precision not in {"fp32", "fp16", "bf16", "int8", "int4"}:
        raise ValueError("unsupported inference precision")
    if context < 128 or concurrency < 1:
        raise ValueError("context must be at least 128 and concurrency at least 1")

    weights_gb = profile.params * DTYPE_BYTES[precision] / GB
    kv_per_sequence = 2 * profile.layers * profile.kv_heads * profile.head_dim * context * kv_bytes / GB
    kv_total = kv_per_sequence * concurrency
    runtime_gb = 1.5 + (weights_gb + kv_total) * 0.08
    total = weights_gb + kv_total + runtime_gb
    plan = _gpu_plan(total * 1.1)
    return {
        "kind": "inference",
        "profile": {"name": profile.name, "params": profile.params, "exact": profile.exact},
        "inputs": {"precision": precision, "context": context, "concurrency": concurrency, "kv_dtype_bytes": kv_bytes},
        "breakdown_gb": {
            "weights": _round_gb(weights_gb),
            "kv_cache": _round_gb(kv_total),
            "runtime_overhead": _round_gb(runtime_gb),
            "total": _round_gb(total),
        },
        "gpu_plan": plan,
        "notes": [
            "Inference estimate excludes batching inefficiency and server-specific CUDA graph/allocator overhead.",
            "KV cache scales linearly with context length and concurrent sequences.",
        ],
    }


def format_estimate(estimate: dict[str, Any]) -> str:
    profile = estimate["profile"]
    inputs = estimate["inputs"]
    breakdown = estimate["breakdown_gb"]
    lines = [f"{profile['name']} · {estimate['kind']} · {inputs}"]
    lines.append("Memory breakdown (GB):")
    for key, value in breakdown.items():
        lines.append(f"  {key.replace('_', ' '):<18} {value:>8.2f}")
    if estimate["kind"] == "training":
        lines.append(f"Trainable parameters: {estimate['trainable_params']:,} ({estimate['trainable_fraction'] * 100:.3f}%)")
    lines.append(f"Recommendation: {estimate['gpu_plan']['label']} GPU")
    if estimate["kind"] == "training":
        lines.append(f"System RAM: {estimate['system_ram_gb']} GB minimum planning target")
        lines.append(f"Storage: {estimate['storage_gb']} GB minimum planning target")
    lines.append("")
    lines.extend(f"- {note}" for note in estimate["notes"])
    return "\n".join(lines)


def planning_table() -> str:
    rows = [
        "| Model | Method | Base weights | Grad + optimizer | Activations | Estimated peak | GPU plan | System RAM | Storage |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    matrix: list[tuple[float, str]] = [
        (1.5, "qlora"),
        (3, "lora"),
        (3, "qlora"),
        (7, "lora"),
        (7, "qlora"),
        (8, "full"),
        (8, "qlora"),
        (13, "qlora"),
        (14, "qlora"),
        (32, "qlora"),
        (70, "qlora"),
    ]
    for params_b, method in matrix:
        estimate = estimate_training(params_b, method=method)
        breakdown = estimate["breakdown_gb"]
        rows.append(
            "| {model}B | {method} | {weights:.1f} | {state:.1f} | {activations:.1f} | {peak:.1f} | {gpu} | {ram} | {storage} |".format(
                model=f"{params_b:g}",
                method=method.upper(),
                weights=breakdown["base_weights"],
                state=breakdown["gradients"] + breakdown["optimizer_state"] + breakdown["logits"],
                activations=breakdown["activations"],
                peak=breakdown["total"],
                gpu=estimate["gpu_plan"]["label"],
                ram=f"{estimate['system_ram_gb']} GB",
                storage=f"{estimate['storage_gb']} GB",
            )
        )
    return "\n".join(rows)
