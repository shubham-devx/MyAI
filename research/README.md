# V-Sutra research workspace

This directory is a standalone, offline-first workspace for preparing future model customization. It is separate from `backend/` and `frontend/`: importing or running its tools does not change the application model, download weights, or start training. There is currently no training runner.

## What is here

- `data/schema/` and `data/examples/`: the `vsutra-instruction-v1` JSONL format, curated instruction examples, evaluation cases, and benchmark prompts.
- `vsutra_research/datasets.py`: deterministic JSONL, schema, duplicate, secret, PII, and split validation.
- `vsutra_research/evaluation.py`: automated checks and manual rubric evaluation against real model responses or a saved predictions file.
- `vsutra_research/benchmark.py`: real endpoint latency/throughput measurements, with environment details and explicit failures.
- `vsutra_research/hardware.py`: planning estimates for training and inference GPU memory, system RAM, and storage.
- `vsutra_research/registry.py`: versioned model manifests, checksums, lineage, and evidence-gated stage transitions.
- `vsutra_research/inference.py`: OpenAI-compatible, Ollama, and V-Sutra inference adapters plus compatibility-manifest generation.
- `configs/`: framework-neutral evaluation, benchmark, and full/LoRA/QLoRA planning templates. These are not executable training jobs.
- `docs/`: [hardware requirements](docs/hardware-requirements.md), [fine-tuning methods](docs/fine-tuning-methods.md), and the [research workflow](docs/research-workflow.md).

## Setup

Use Python 3.10 or newer. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

No GPU framework, model weights, cloud credentials, or training dependencies are installed by this workspace.

## Validate and plan

Run these commands from `research/` so template paths resolve as written:

```powershell
python -m vsutra_research validate-dataset --dataset data/examples/instructions.jsonl --require-verified
python -m vsutra_research check-config --path configs/training-qlora-7b.yaml
python -m vsutra_research hardware --params 7 --method qlora --seq-len 2048 --batch-size 1 --rank 16 --modules 4
python -m vsutra_research evaluate --config configs/eval.yaml --predictions outputs/predictions.jsonl --label baseline
python -m vsutra_research benchmark --config configs/benchmark.yaml --dry-run
```

Evaluation and benchmark commands without `--predictions` or `--dry-run` call the configured inference endpoint. They do not train. Reports belong under `reports/` and run artifacts under `outputs/`; do not commit private datasets, credentials, downloaded weights, or large checkpoints.

## Dataset contract

Each non-empty JSONL line is one independent object. See `data/schema/instruction.schema.json` and `data/examples/instructions.jsonl`. Every example records a stable ID, provenance, license/usage right, language, conversation messages, and verification metadata. Use `split` values `train`, `validation`, or `test`; keep the held-out evaluation set separate from training. Group related prompts and source documents into the same split before deduplication to reduce leakage. Validate new data offline before a human reviewer approves it.

Tool-call examples use assistant `tool_calls` followed by matching `tool` messages. Tool results in authored data must be real, reproducible, and permitted for training; never fabricate tool outputs. Treat all user-provided content as untrusted data. Do not include secrets, private personal data, or material whose terms do not permit model training.

## No training yet

The `configs/training-*.yaml` files document experiment intent only. The CLI has no `train` command and the package does not import a training framework. Before a future training runner is added, choose an exact base-model revision and license, prepare independently reviewed train/validation/test splits, record actual hardware, run a short measured smoke test, and pass baseline evaluation and compatibility checks. Read the hardware guide before adopting any template values.

The main app continues using its configured provider/model. A future customized model should first be exported to a separate endpoint and connected through `CUSTOM_MODEL_*` only after compatibility and quality evidence are reviewed. Changing the default model is a separate deployment decision.
