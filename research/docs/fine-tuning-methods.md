# LoRA and QLoRA research notes

This is background for future experiment selection. It does not install training libraries or start a run. The project currently has framework-neutral config templates only.

## Full fine-tuning

All base-model parameters are trainable. This offers the most direct adaptation, but requires gradients and optimizer state for the full model, creates large checkpoints, and makes rollback/storage expensive. Reserve it for small models or a later, measured infrastructure plan. The `training-full-0.5b.yaml` file is a comparison template, not an endorsement to train immediately.

## LoRA

Low-Rank Adaptation freezes the base model and trains small rank-decomposition matrices on selected linear layers. It reduces optimizer state and checkpoint size; the frozen base model still occupies GPU memory at its training precision. Rank, alpha, dropout, and target modules are experiment variables. Start with attention projections and expand modules only when evaluation shows a reason. Keep the base revision fixed so an adapter cannot silently be paired with different weights.

## QLoRA

QLoRA loads frozen base weights in a low-bit representation (commonly NF4 with double quantization) and trains LoRA adapters using a higher-precision compute dtype such as bf16 or fp16. This lowers base-weight VRAM use, while activations, kernels, optimizer state, context length, and runtime overhead still matter. It does not make every model size fit on a consumer GPU.

The current 7B template uses QLoRA, rank 16, attention projections, micro-batch 1, sequence length 2,048, gradient checkpointing, and a 24 GB VRAM / 32 GB RAM / 80 GB storage planning target. This is deliberately more conservative than the current arithmetic estimator. Confirm hardware with an actual smoke run before calling it recommended.

## Experiment design

- Pin an exact base model revision and review its license, acceptable-use terms, and redistribution conditions.
- Deduplicate before splitting. Group paraphrases, related conversations, and examples from the same source into one split to avoid leakage.
- Keep a stable test set untouched by prompt tuning and training. Use a separate validation set for checkpoint selection.
- Compare against the same base model using the same evaluation cases and benchmark settings. Include safety/refusal, formatting, tool-use, and task-specific cases relevant to the product.
- Change one major variable at a time where practical. Record context length, effective batch, optimizer, precision, rank, target modules, seed, library versions, and measured resource peaks.
- Save adapters separately from base weights and retain a reproducible manifest linking the adapter to its dataset, config, code revision, and reports.

## Why this workspace does not train

No training framework or execution command is included. The YAML templates are planning records validated by `check-config`; they are not passed to a hidden trainer. A future training implementation should be added as a separate research-only package/command with explicit dependency isolation, artifact paths, cancellation, resource checks, and a measured hardware gate.

## References

- [QLoRA paper (NeurIPS 2023)](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1feb87871436031bdc0f2beaa62a049b-Abstract.html) describes NF4, double quantization, paged optimizers, and low-memory adapter tuning.
- [Hugging Face PEFT documentation](https://huggingface.co/docs/transformers/peft) documents adapter training and supported model/module configuration.
- [Hugging Face LoRA conceptual guide](https://huggingface.co/docs/peft/main/conceptual_guides/lora) explains target modules and adapter behavior.
