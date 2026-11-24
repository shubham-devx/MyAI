# Troubleshooting

## No models available

Check `GET http://127.0.0.1:8000/health`, start Ollama with `ollama serve`, and verify the configured model is installed with `ollama list`. Confirm `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in `backend/.env`, then restart FastAPI. A running API with an unavailable Ollama service can still serve status and deterministic local tools.

## Browser cannot reach the API

Confirm `frontend/.env.local` points to the correct API base URL and restart Next.js after changing it. Set `FRONTEND_ORIGINS` to the exact browser origin, including scheme and port. Check that the backend is bound to an address reachable from the browser. CORS does not provide authentication or network reachability.

## HTTP 413 or 429

413 means a request exceeded `MAX_REQUEST_BODY_MB` (default 128 MB) or a route-specific upload limit. Reduce the upload size/count. 429 means the per-IP, in-memory sliding-window limit was reached (defaults: 120 general, 12 chat, 10 upload requests per minute); honor `Retry-After`. 503 on inference means all `MAX_CONCURRENT_INFERENCE_REQUESTS` slots are occupied. Limits are per process and reset on restart; do not rely on them as a shared production rate limiter.

## Upload is rejected or has no extracted text

Document uploads accept PDF, DOCX, TXT, and CSV with route-specific size, count, and extracted-text bounds. PDFs may be image-only; OCR is not provided. DOCX archives that expand beyond the extraction cap are rejected. CSV analysis expects UTF-8. Re-save the file in a supported format and remove unnecessary content.

## Chat stream stalls or ends unexpectedly

Check provider health, model logs, network/proxy idle timeouts, and backend errors. The frontend reports a stream that closes before a completion event as incomplete. Press Stop to abort, then retry. Streaming providers and reverse proxies must preserve `text/event-stream` and disable response buffering/short idle timeouts.

## Tool or sandbox unavailable

Open the Toolkit panel and read the per-tool status reason. Search requires a backend-configured provider. Code execution requires the configured Docker/Podman engine to be reachable and the selected image to be present locally. No host fallback exists. Every model-requested run requires explicit approval.

## Slow inference or high memory use

Separate cold model load time from warm generation. Record model, quantization, context, concurrency, prompt/output token counts, time-to-first-token, tokens/second, GPU VRAM, and host RAM. Check Ollama/provider metrics and GPU telemetry. V-Sutra currently does not schedule, unload, or tune the model runtime. Reduce concurrency or context in the model server only as a deliberate configuration change.

## Tests fail

Run backend tests from `backend/` and frontend tests from `frontend/` as described in [local development](local-development.md). Check the first failing assertion and dependency versions; do not treat skipped external integrations as passing validation.
