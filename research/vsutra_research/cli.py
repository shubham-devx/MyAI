"""Command line entry point for the V-Sutra research workspace."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .benchmark import environment_info, load_prompts, run_benchmark_async, write_report as write_benchmark_report
from .config import (
    ConfigError,
    format_config_check,
    load_yaml,
    validate_benchmark_config,
    validate_eval_config,
    validate_training_config,
)
from .datasets import format_summary, validate_dataset
from .evaluation import (
    evaluate,
    evaluate_with_backend_async,
    load_eval_cases,
    load_predictions,
    write_report as write_eval_report,
)
from .hardware import estimate_inference, estimate_training, format_estimate, planning_table
from .inference import InferenceError, build_backend, compatibility_manifest, manifest_env_lines, ollama_modelfile
from .registry import (
    RegistryError,
    attach_benchmark,
    attach_evaluation,
    create_manifest,
    format_manifest,
    list_manifests,
    load_manifest,
    promote,
    verify_manifest,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = WORKSPACE_ROOT / "data" / "schema" / "instruction.schema.json"
DEFAULT_REGISTRY = WORKSPACE_ROOT / "registry"


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _config_command(args: argparse.Namespace) -> int:
    config = load_yaml(args.path)
    if args.kind == "training":
        errors, warnings = validate_training_config(config)
    elif args.kind == "eval":
        errors, warnings = validate_eval_config(config)
    else:
        errors, warnings = validate_benchmark_config(config)
    print(format_config_check(str(args.path), errors, warnings))
    return 0 if not errors else 1


def _hardware_command(args: argparse.Namespace) -> int:
    if args.table:
        print(planning_table())
        return 0
    if args.params is None:
        print("Error: --params is required unless --table is used.", file=sys.stderr)
        return 2
    estimate = estimate_training(
        args.params,
        method=args.method,
        precision=args.precision,
        seq_len=args.seq_len,
        batch_size=args.batch_size,
        gradient_accumulation=args.grad_accum,
        checkpointing=not args.no_checkpointing,
        lora_rank=args.rank,
        lora_modules=args.modules,
        optimizer=args.optimizer,
        offload_optimizer=args.offload_optimizer,
        save_limit=args.save_limit,
        dataset_gb=args.dataset_gb,
    )
    if args.json:
        _print_json(estimate)
    else:
        print(format_estimate(estimate))
    return 0


def _inference_estimate_command(args: argparse.Namespace) -> int:
    estimate = estimate_inference(
        args.params,
        precision=args.precision,
        context=args.context,
        concurrency=args.concurrency,
    )
    if args.json:
        _print_json(estimate)
    else:
        print(format_estimate(estimate))
    return 0


def _validate_dataset_command(args: argparse.Namespace) -> int:
    report = validate_dataset(
        args.dataset,
        args.schema,
        min_examples=args.min_examples,
        max_examples=args.max_examples,
        report_path=args.report,
        allow_duplicates=args.allow_duplicates,
        require_verified=args.require_verified,
    )
    print(format_summary(report))
    if args.report:
        print(f"\nReport written to {args.report}")
    return 0 if not report.errors and report.examples >= report.min_examples else 1


def _evaluate_command(args: argparse.Namespace) -> int:
    config: dict[str, Any] = {}
    dataset_path = args.dataset
    output_dir = args.output
    backend_config: dict[str, Any] = {}
    if args.config:
        config = load_yaml(args.config)
        errors, warnings = validate_eval_config(config)
        for warning in warnings:
            print(f"Warning: {warning}")
        if errors:
            for error in errors:
                print(f"Error: {error}", file=sys.stderr)
            return 1
        dataset_path = dataset_path or config.get("dataset")
        output_dir = output_dir or config.get("output_dir")
        backend_config = config.get("backend") if isinstance(config.get("backend"), dict) else {}
    if not dataset_path:
        print("Error: provide --dataset or a config with a dataset.", file=sys.stderr)
        return 2
    if not output_dir:
        output_dir = str(WORKSPACE_ROOT / "reports" / "eval")

    cases, problems = load_eval_cases(dataset_path)
    if args.limit:
        cases = cases[: args.limit]
    if problems or not cases:
        for problem in problems:
            print(f"Dataset problem: {problem}", file=sys.stderr)
        if not cases:
            print("Error: no usable evaluation cases.", file=sys.stderr)
            return 1

    if args.predictions:
        predictions, prediction_problems = load_predictions(args.predictions)
        if prediction_problems:
            for problem in prediction_problems:
                print(f"Prediction problem: {problem}", file=sys.stderr)
        report = evaluate(
            cases,
            predictions,
            model_label=args.label or "supplied predictions",
            source=f"predictions file: {args.predictions}",
            dataset_path=str(dataset_path),
            extra_issues=problems + prediction_problems,
        )
    else:
        kind = str(backend_config.get("kind", "none")).lower()
        if kind == "none":
            print("Error: configure a backend or pass --predictions for offline scoring.", file=sys.stderr)
            return 2
        try:
            backend = build_backend(kind, backend_config)
        except InferenceError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
        report, _ = asyncio.run(evaluate_with_backend_async(
            cases,
            backend,
            model_label=backend.name,
            dataset_path=str(dataset_path),
            temperature=float(backend_config.get("temperature", 0)),
            max_tokens=int(backend_config.get("max_tokens", 512)),
            seed=backend_config.get("seed") if isinstance(backend_config.get("seed"), int) else None,
        ))
        report["issues"].extend(problems)

    json_path, markdown_path = write_eval_report(report, output_dir, stem=args.stem)
    summary = report["summary"]
    print(f"Cases: {summary['cases']} · automated {summary['automated']} · passed {summary['passed']} · failed {summary['failed']} · manual {summary['manual_pending']}")
    print(f"Automatic accuracy: {summary['accuracy'] if summary['accuracy'] is not None else 'not available'}")
    print(f"Reports: {json_path} · {markdown_path}")
    return 0


def _benchmark_command(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    errors, warnings = validate_benchmark_config(config)
    for warning in warnings:
        print(f"Warning: {warning}")
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    prompts, problems = load_prompts(str(config["prompts"]))
    if problems:
        for problem in problems:
            print(f"Prompt problem: {problem}", file=sys.stderr)
    if not prompts:
        print("Error: no usable benchmark prompts.", file=sys.stderr)
        return 1
    backend_config = config.get("backend") or {}
    kind = str(backend_config.get("kind", "")).lower()
    try:
        backend = build_backend(kind, backend_config)
    except InferenceError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    repeats = int(config.get("repeats", 3))
    warmup = int(config.get("warmup", 1))
    max_tokens = int(config.get("max_tokens", 256))
    temperature = float(config.get("temperature", 0))
    seed = config.get("seed") if isinstance(config.get("seed"), int) else None
    if args.dry_run:
        print(f"Backend: {backend.name}")
        print(f"Prompts: {len(prompts)} · repeats {repeats} · warmup {warmup} · planned measured requests {len(prompts) * repeats}")
        print(f"Max output tokens: {max_tokens} · temperature {temperature}")
        _print_json(environment_info())
        print("Dry run only: no requests were sent and no performance numbers were produced.")
        return 0
    report = asyncio.run(run_benchmark_async(
        prompts,
        backend,
        repeats=repeats,
        warmup=warmup,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
    ))
    report["prompt_issues"] = problems
    output_dir = args.output or config.get("output_dir") or str(WORKSPACE_ROOT / "reports" / "benchmark")
    json_path, markdown_path = write_benchmark_report(report, output_dir, stem=args.stem)
    if report.get("summary"):
        summary = report["summary"]
        print(f"Success: {summary['successes']}/{summary['requests']}")
        print(f"TTFT p50: {summary['ttft_ms']['median']} ms · latency p50: {summary['latency_ms']['median']} ms · tokens/s aggregate: {summary['tokens_per_second']['aggregate']}")
    else:
        print("No requests succeeded; no performance statistics were produced.")
    print(f"Reports: {json_path} · {markdown_path}")
    return 0 if report.get("summary") else 1


def _compat_command(args: argparse.Namespace) -> int:
    capabilities = [item.strip() for item in str(args.capabilities).split(",") if item.strip()]
    try:
        manifest = compatibility_manifest(
            name=args.name,
            version=args.version,
            base_url=args.base_url,
            model=args.model,
            context_window=args.context_window,
            capabilities=capabilities,
            tool_support_verified=args.tool_support_verified,
            vision_support_verified=args.vision_support_verified,
            notes=args.notes,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    if args.format == "env":
        print("\n".join(manifest_env_lines(manifest)))
    elif args.format == "json":
        _print_json(manifest)
    else:
        if not args.gguf:
            print("Error: --gguf is required for the modelfile format.", file=sys.stderr)
            return 2
        print(ollama_modelfile(gguf_path=args.gguf, system=args.system, template=args.template))
    return 0


def _registry_command(args: argparse.Namespace) -> int:
    if args.registry_action == "create":
        path = create_manifest(
            args.root,
            name=args.name,
            version=args.version,
            base_model=args.base_model,
            method=args.method,
            dataset_path=args.dataset,
            config_path=args.config,
            workspace=WORKSPACE_ROOT,
            base_revision=args.base_revision,
            notes=args.notes,
        )
        print(f"Created {path}")
        print(format_manifest(load_manifest(path)))
        return 0
    if args.registry_action == "list":
        manifests = list_manifests(args.root)
        if not manifests:
            print("No model manifests yet.")
            return 0
        for item in manifests:
            accuracy = item["accuracy"] if item["accuracy"] is not None else "not evaluated"
            print(f"{item['name']} {item['version']} · {item['stage']} · {item['method']} · {item['dataset_examples']} examples · accuracy {accuracy}")
        return 0
    if args.registry_action == "verify":
        errors = verify_manifest(args.manifest, WORKSPACE_ROOT)
        if errors:
            for error in errors:
                print(f"Error: {error}", file=sys.stderr)
            return 1
        print("Manifest verified: hashes and required fields match.")
        return 0
    if args.registry_action == "attach-eval":
        path = attach_evaluation(args.manifest, args.report, workspace=WORKSPACE_ROOT)
        print(f"Attached evaluation evidence to {path}")
        return 0
    if args.registry_action == "attach-benchmark":
        path = attach_benchmark(args.manifest, args.report, workspace=WORKSPACE_ROOT)
        print(f"Attached benchmark evidence to {path}")
        return 0
    if args.registry_action == "promote":
        errors = verify_manifest(args.manifest, WORKSPACE_ROOT)
        if errors:
            for error in errors:
                print(f"Error: {error}", file=sys.stderr)
            return 1
        path = promote(args.manifest, args.stage, min_accuracy=args.min_accuracy, note=args.note)
        print(f"Promoted to {args.stage}: {path}")
        print(format_manifest(load_manifest(path)))
        return 0
    print("Error: unknown registry action.", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vsutra_research",
        description="Offline research, evaluation, hardware planning, and model registry tools. This workspace never starts training.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-dataset", help="Validate a JSONL instruction dataset.")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    validate.add_argument("--report", default=None)
    validate.add_argument("--min-examples", type=int, default=1)
    validate.add_argument("--max-examples", type=int, default=500_000)
    validate.add_argument("--allow-duplicates", action="store_true")
    validate.add_argument("--require-verified", action="store_true")
    validate.set_defaults(handler=_validate_dataset_command)

    hardware = subparsers.add_parser("hardware", help="Estimate training memory and storage.")
    hardware.add_argument("--params", type=float, default=None, help="Model size in billions of parameters.")
    hardware.add_argument("--method", choices=("lora", "qlora", "full"), default="qlora")
    hardware.add_argument("--precision", choices=("bf16", "fp16", "fp32"), default="bf16")
    hardware.add_argument("--seq-len", type=int, default=2048)
    hardware.add_argument("--batch-size", type=int, default=1)
    hardware.add_argument("--grad-accum", type=int, default=8)
    hardware.add_argument("--no-checkpointing", action="store_true")
    hardware.add_argument("--rank", type=int, default=16)
    hardware.add_argument("--modules", type=int, default=4)
    hardware.add_argument("--optimizer", choices=("adamw", "adamw_8bit", "sgd", "none"), default=None)
    hardware.add_argument("--offload-optimizer", action="store_true")
    hardware.add_argument("--save-limit", type=int, default=2)
    hardware.add_argument("--dataset-gb", type=float, default=1.0)
    hardware.add_argument("--json", action="store_true")
    hardware.add_argument("--table", action="store_true", help="Print the planning matrix.")
    hardware.set_defaults(handler=_hardware_command)

    inference = subparsers.add_parser("inference-plan", help="Estimate inference VRAM.")
    inference.add_argument("--params", type=float, required=True)
    inference.add_argument("--precision", choices=("bf16", "fp16", "fp32", "int8", "int4"), default="int4")
    inference.add_argument("--context", type=int, default=8192)
    inference.add_argument("--concurrency", type=int, default=1)
    inference.add_argument("--json", action="store_true")
    inference.set_defaults(handler=_inference_estimate_command)

    check = subparsers.add_parser("check-config", help="Validate a template without running it.")
    check.add_argument("--path", required=True)
    check.add_argument("--kind", choices=("training", "eval", "benchmark"), default="training")
    check.set_defaults(handler=_config_command)

    evaluate_parser = subparsers.add_parser("evaluate", help="Score real responses or a predictions file.")
    evaluate_parser.add_argument("--config", default=None)
    evaluate_parser.add_argument("--dataset", default=None)
    evaluate_parser.add_argument("--predictions", default=None)
    evaluate_parser.add_argument("--output", default=None)
    evaluate_parser.add_argument("--label", default=None)
    evaluate_parser.add_argument("--limit", type=int, default=None)
    evaluate_parser.add_argument("--stem", default="eval")
    evaluate_parser.set_defaults(handler=_evaluate_command)

    benchmark_parser = subparsers.add_parser("benchmark", help="Measure latency and throughput against a real endpoint.")
    benchmark_parser.add_argument("--config", required=True)
    benchmark_parser.add_argument("--output", default=None)
    benchmark_parser.add_argument("--stem", default="benchmark")
    benchmark_parser.add_argument("--dry-run", action="store_true")
    benchmark_parser.set_defaults(handler=_benchmark_command)

    compat = subparsers.add_parser("compat", help="Generate a truthful inference compatibility manifest.")
    compat.add_argument("--name", required=True)
    compat.add_argument("--version", required=True)
    compat.add_argument("--base-url", required=True)
    compat.add_argument("--model", required=True)
    compat.add_argument("--context-window", type=int, required=True)
    compat.add_argument("--capabilities", default="chat,streaming")
    compat.add_argument("--tool-support-verified", action="store_true")
    compat.add_argument("--vision-support-verified", action="store_true")
    compat.add_argument("--format", choices=("env", "json", "modelfile"), default="env")
    compat.add_argument("--gguf", default=None)
    compat.add_argument("--system", default="")
    compat.add_argument("--template", default="")
    compat.add_argument("--notes", default="")
    compat.set_defaults(handler=_compat_command)

    registry = subparsers.add_parser("registry", help="Manage model manifests and evidence-gated promotion.")
    registry.add_argument("--root", default=str(DEFAULT_REGISTRY))
    registry_sub = registry.add_subparsers(dest="registry_action", required=True)
    create = registry_sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--base-model", required=True)
    create.add_argument("--base-revision", default="")
    create.add_argument("--method", choices=("full", "lora", "qlora"), required=True)
    create.add_argument("--dataset", required=True)
    create.add_argument("--config", required=True)
    create.add_argument("--notes", default="")
    registry_sub.add_parser("list")
    verify = registry_sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    attach_eval = registry_sub.add_parser("attach-eval")
    attach_eval.add_argument("--manifest", required=True)
    attach_eval.add_argument("--report", required=True)
    attach_bench = registry_sub.add_parser("attach-benchmark")
    attach_bench.add_argument("--manifest", required=True)
    attach_bench.add_argument("--report", required=True)
    promote_parser = registry_sub.add_parser("promote")
    promote_parser.add_argument("--manifest", required=True)
    promote_parser.add_argument("--stage", choices=("evaluated", "canary", "production", "retired"), required=True)
    promote_parser.add_argument("--min-accuracy", type=float, default=0.0)
    promote_parser.add_argument("--note", default="")
    registry.set_defaults(handler=_registry_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, RegistryError, InferenceError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
