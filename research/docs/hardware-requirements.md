# Training hardware requirements

This guide documents resource requirements before selecting a training template. V-Sutra has no measured training runs yet, so the values below are conservative planning targets, not benchmark results or guarantees. They assume one modern NVIDIA GPU, Linux with a supported CUDA/PyTorch stack, sequence length 2,048, micro-batch 1, gradient checkpointing, and a small curated dataset. Exact model architecture, attention implementation, optimizer, framework, and dataset size can change peak usage substantially.

## Conservative starting targets

| Workload | GPU VRAM target | System RAM target | Free storage target | Notes |
| --- | ---: | ---: | ---: | --- |
| 1–2B QLoRA | 12 GB | 16–32 GB | 30 GB | Small models are useful for pipeline smoke runs; quality is not representative of a larger base. |
| 3B QLoRA | 16 GB | 32 GB | 50 GB | Prefer 24 GB if using longer sequences, larger batches, or an expanded target-module set. |
| 7B QLoRA | 24 GB | 32 GB | 80 GB | Matches the conservative target in `configs/training-qlora-7b.yaml`; 12 GB estimator results are not a safe hardware recommendation. |
| 7B LoRA, base in bf16/fp16 | 40 GB | 48 GB | 100 GB | A 24 GB card may work only with constrained settings and a measured proof; do not assume it. |
| 0.5B full fine-tuning | 24 GB | 32 GB | 80 GB | Full optimizer states and checkpoints dominate; use a measured run before increasing context or batch size. |

These are single-GPU targets. Multi-GPU sharding is not equivalent to adding VRAM: distributed training adds communication, software, and failure-mode requirements. The current estimator's multi-GPU output is capacity arithmetic only, not a distributed-training plan.

## What storage includes

Keep at least the table's free space available on fast local storage. Budget for the downloaded base model and tokenizer, dataset copies and processed caches, temporary checkpoints, final adapter or full checkpoint, exported/quantized artifacts, and logs/reports. Keep raw source data separately where retention policy requires it. For a 7B QLoRA experiment, the 80 GB target gives room for the source weights, caches, and multiple checkpoints; the adapter itself is much smaller than the base model.

## Estimator limits and measurement gate

`python -m vsutra_research hardware ...` estimates weights, trainable parameters, optimizer state, activations, logits, RAM, and storage. Its formulas are intentionally transparent and planning-grade. They do not model every CUDA allocator reservation, kernel workspace, fragmentation, framework version, tokenizer behavior, or serving/training overlap. The estimate can underpredict real peak GPU memory; its output must not be treated as proof that a device will fit a run.

Before recommending or launching a real configuration:

1. Record the exact GPU model/count, VRAM, driver/CUDA stack, host RAM, free disk, base-model revision, and dataset size.
2. Validate the dataset and config; keep validation/test examples out of training.
3. Run a short smoke experiment on the exact hardware and settings, recording peak allocated and reserved VRAM, peak host RAM, disk use, tokens/sec, and failures.
4. Keep at least 20% VRAM headroom over the measured peak for allocator/runtime variation; lower sequence length or batch size only as a documented experiment change.
5. Mark `hardware.measured: true` only after recording those measurements and the run identifier. Until then, leave it false and call the configuration provisional.

The config validator warns when a template's hardware is unmeasured. No template should be called a final recommendation until the smoke-run evidence and evaluation gate exist.

## References

- [QLoRA paper (NeurIPS 2023)](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1feb87871436031bdc0f2beaa62a049b-Abstract.html) demonstrates the method on a 65B model with a 48 GB GPU; this is evidence that low-bit adapter tuning can reduce memory substantially, not a sizing guarantee for a different model or setup.
- [Hugging Face bitsandbytes quantization guide](https://huggingface.co/docs/transformers/en/quantization/bitsandbytes) describes NF4 and nested/double quantization options.
