# Research workflow and model lifecycle

The research workspace produces evidence and manifests; it does not train, deploy, or change the application default. Keep all generated checkpoints and reports outside the main application source tree.

## Dataset preparation

1. Author JSONL records using `data/schema/instruction.schema.json` and `vsutra-instruction-v1`.
2. Record provenance and a license/usage right for every example. Mark `metadata.verified` only after human review.
3. Assign train/validation/test splits before tuning. Keep evaluation cases in `data/examples/eval-cases.jsonl` or a separate held-out file, not in the train path.
4. Run `python -m vsutra_research validate-dataset --dataset <path> --require-verified --report reports/data-validation.json`.
5. Resolve all errors, duplicates, secret findings, and privacy warnings. Do not bypass a privacy warning without documented review.

## Baseline and candidate evaluation

Capture a baseline on the untouched base model before fine-tuning. Use `evaluate --config configs/eval.yaml` against a real endpoint, or score a saved JSONL predictions file offline. Rubric cases are marked for manual review; automated exact/contains/regex/schema/tool checks are not a substitute for human review. Benchmark separately with a fixed prompt set and machine/server settings. Reports contain only results actually observed during that run.

## Versioning and promotion

The registry stores manifests, not weights. Each candidate should have a stable lowercase name and semantic version, base model and exact revision, fine-tuning method, dataset path/hash and count, config path/hash, code revision, artifact location/hash, creation time, notes, evaluation report, and benchmark report. Use immutable versioned artifact directories; never overwrite a promoted artifact.

Lifecycle stages are `draft -> evaluated -> canary -> production -> retired`. The registry requires an evaluation report before `evaluated`, a configured accuracy threshold before `canary` or `production`, and a benchmark with successful requests before `production`. Attach reports from real runs; template values are not evidence. Record a rollback target before canary deployment.

## Inference compatibility

Serve the candidate behind a private OpenAI-compatible chat-completions endpoint or export a GGUF for Ollama. Generate a compatibility manifest with `vsutra_research compat`; declare tool or vision capabilities only after verifying those exact behaviors against the candidate endpoint. Keep provider credentials on the backend. Add a candidate as a separately named custom provider/model first, compare it in evaluation and canary use, and only change the default after explicit deployment review. The main app's current local/provider settings are not changed by research commands.
