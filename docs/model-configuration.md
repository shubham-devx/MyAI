# Model configuration

The backend owns provider configuration and secrets. The browser receives model IDs and availability, not credentials. Ollama is the local default; remote providers remain opt-in.

## Ollama

Set `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in `backend/.env`. The registry discovers installed models from Ollama and queries model metadata where available. Pull the desired model separately with `ollama pull <model>`. Unknown context windows and capabilities should remain unknown; do not infer tool or vision support from a model name.

Ollama manages model residency and unloading. V-Sutra currently does not pin a model, call an unload endpoint, or expose `keep_alive`, GPU layer, thread, or context tuning. The API caps chat, code-generation, and structured-output requests together per process with `MAX_CONCURRENT_INFERENCE_REQUESTS` (default 2) and returns HTTP 503 when full; it does not queue requests. Load time, token throughput, GPU utilization, and unload behavior depend on the Ollama version, model quantization, host, and concurrent workloads. Measure the deployed configuration rather than assuming GPU use.

## Optional providers

Configure provider-specific API keys and model catalogs in `backend/.env` using the exact variable names in [`backend/.env.example`](../backend/.env.example):

| Provider | Required settings | Notes |
| --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY`; optionally `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODELS` | Catalog entries are configured by the operator. |
| Mistral | `MISTRAL_API_KEY`; optionally `MISTRAL_BASE_URL`, `MISTRAL_MODELS` | Uses the provider's OpenAI-compatible chat API. |
| Amazon Bedrock | `BEDROCK_REGION`, `BEDROCK_MODELS` | Uses the AWS SDK credential chain; do not paste AWS secrets into the frontend. |
| Custom | `CUSTOM_MODEL_NAME`, `CUSTOM_MODEL_BASE_URL`; optionally model ID, API key, context window, capabilities | Use a trusted private OpenAI-compatible endpoint and declare only tested capabilities. |

Restart the backend after changing its environment. Search and speech providers have separate `SEARCH_*` and `VOICE_*` settings in the same example file.

Never use `NEXT_PUBLIC_*` for keys. The app does not automatically make a cloud inference request: a user must select an available remote model and submit a prompt.

## Resource planning and monitoring

Model weights and the inference runtime are external to this repository. GPU VRAM and system RAM use depend on model size, quantization, context length, KV cache, batching, server settings, and concurrency. The backend currently has no admission queue or GPU scheduler; Ollama/provider limits apply. Check `ollama ps`, provider/runtime metrics, GPU vendor telemetry, process RSS, and request timings on the target machine. Compare time-to-first-token and tokens/second using the separate research benchmark workspace; record warm-up and cold-start runs separately.

No infrastructure benchmark or GPU utilization measurement has been run as part of this guide. Do not use it as a performance claim.
