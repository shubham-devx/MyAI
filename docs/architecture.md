# Architecture

The Next.js App Router frontend owns presentation and calls the FastAPI service at `NEXT_PUBLIC_API_BASE_URL`. Its chat client sends prompts to `/api/chat/stream`, reads server-sent events (SSE), and renders every incoming token, tool event, notice, and citation. FastAPI adapts each provider's native stream to a small provider-neutral event model. CORS permits configured frontend origins with credentials. The `ModelProvider` interface isolates model transport, so a custom local or hosted provider can be added without coupling routes to a vendor. Model weights and the Ollama runtime remain external. Account/session metadata live in local SQLite for development or private Supabase Postgres for deployment; transcripts remain browser-local.

```text
Browser (Next.js) --POST + SSE--> FastAPI --stream--> ModelProvider
                                        │                ├─ Ollama --> local model
                                        │                ├─ OpenAI-compatible (DeepSeek/Mistral/custom)
                                        │                └─ Bedrock ConverseStream
                                        │
                                        ├─ tool loop: schemas -> tool calls -> tool results -> final answer
                                        ├─ tools: calculator, web search, CSV analysis, container sandbox
                                        ├─ confirmation broker: pauses consequential run_code calls
                                        ├─ structured output: provider JSON mode + JSON Schema validation
                                        └─ voice proxy: configurable OpenAI-compatible STT/TTS
```

## Chat and tool flow

1. The frontend sends recent messages, the selected model, a conversation ID, the context limit, and the user's enabled tool names.
2. FastAPI resolves the provider, adds trusted system context for retrieved document excerpts and attached CSV IDs, and starts the provider event stream.
3. Text fragments become `token` events. Provider-native tool calls become `tool_request` events and are executed only if the user enabled that tool for the request.
4. Tool output is bounded and appended as a normalized `tool` message before the model is asked to continue. The loop is capped by `TOOLS_MAX_ROUNDS`; when the cap is reached, the model must answer with the information already gathered.
5. Consequential `run_code` calls emit `tool_confirmation` and wait on the in-memory broker. The browser approves or denies through `POST /api/tool-confirmations/{id}`. Denial, timeout, or disconnect results in a tool result that says the code did not run.
6. Every tool result, status, and web source is emitted to the UI and included in the final `done` event. A failed tool is reported as a failure; the model is instructed not to fabricate its output.
7. The frontend persists transcript text and tool summaries in browser localStorage. Uploaded document contents, parsed CSV tables, and tool confirmation state remain backend process memory only. Authenticated conversation IDs are tracked in the configured auth database to prevent cross-account document access.

## Safety boundaries

- Tool execution is backend-only. The frontend never receives provider credentials, search keys, or speech-engine keys.
- Web search is an explicit external request and is available only when the operator configures a provider. Responses using it are marked as external and show their sources.
- The calculator is a strict allow-listed AST evaluator; it never uses `eval`.
- Sandboxed code runs only in Docker or Podman with no network, a read-only root filesystem, a bounded tmpfs, a non-root user, no capabilities, no privilege escalation, and CPU/memory/PID/output/time limits. There is no host subprocess fallback.
- Structured output is validated against the caller's JSON Schema after generation. Invalid output is repaired at most once and then rejected.
- Uploaded text and OCR-extracted document content are treated as untrusted reference material, not instructions.
