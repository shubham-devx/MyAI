# V-Sutra — project handoff

This README is the source of truth for the product direction, architecture, implementation decisions, and local runbook. Read it before changing code. Keep it current when the implementation or decisions change so another developer or AI coding assistant can continue without relying on chat history.

## Engineering guides

- [Installation](docs/installation.md)
- [Local development](docs/local-development.md)
- [Model configuration](docs/model-configuration.md)
- [Deployment and exposure boundaries](docs/deployment.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture and security boundaries](docs/architecture.md)

The guides describe current behavior and known limits. This repository has not been declared production-ready; see the test and infrastructure notes below and in the deployment guide.

## Vision

V-Sutra is an independent, local-first AI workspace. Text-only chat works without an account. Signing in unlocks uploads, tools, voice, and workspace settings. Ollama remains the default local model path; account/session metadata uses local SQLite in development and can use Supabase Postgres in deployment while conversation transcripts remain in the browser.

The frontend should depend on a stable V-Sutra API, not on a particular vendor's API. Replacing Ollama with DeepSeek, Bedrock, Mistral, or a model on the user's own infrastructure should happen behind backend provider adapters, without rewriting the chat UI. Provider differences belong in the backend adapters; model IDs, model metadata, availability, and streamed text are normalized at the V-Sutra API boundary.

### Product principles

- Local Ollama remains usable when every cloud setting is blank.
- Cloud providers are optional and never selected automatically. A cloud inference request happens only after a user selects that provider's model and sends a message.
- API keys and provider credentials stay on the backend. Never add secrets to browser code or `NEXT_PUBLIC_*` environment variables.
- Use real provider output. Do not present sample or mock content as AI responses.
- Store conversation transcripts in browser `localStorage`; they are not synced to accounts. SQLite locally or a private Supabase Postgres schema stores account credentials (as password hashes), hashed session/reset tokens, and conversation ownership metadata.
- Require sign-in for document/image uploads, AI tools, voice features, response copy/export, and settings. Enforce the same access checks in the backend.
- Brevo password reset is configurable through backend-only environment variables; reset links are single-use and expire after 30 minutes.
- Uploaded document text exists only in backend process memory with a four-hour inactivity expiry. Image bytes are request-only and are never saved in browser storage or the backend document cache.
- Preserve accessibility, keyboard use, clear errors, and the restrained dark visual system.

## Current implementation

- Next.js App Router frontend with a model selector, streaming chat, conversation context, Stop generation, and provider/model status.
- Functional Settings for theme, text size, response style, active model, provider status, context-turn limit, and clearing current or all temporary history.
- Responsive desktop/tablet/mobile layouts, with a mobile navigation drawer and settings bottom sheet.
- FastAPI backend with health, model catalog, regular completion, and SSE streaming routes.
- Email/password signup, login, logout, persistent sessions, and one-time password reset links sent through Brevo when configured.
- Guest chat is text-only; extended endpoints and image/tool chat require an authenticated session. Document conversation IDs are associated with the owning account.
- Ollama adapter discovers installed models and reports context/capabilities from Ollama where available.
- Optional adapters are registered for DeepSeek, Amazon Bedrock, Mistral, and custom OpenAI-compatible infrastructure.
- The backend catalog identifies unconfigured providers and models. The UI disables unavailable model entries and does not preselect a cloud model.
- Multiple conversations are created, renamed, selected, and deleted in the browser. Transcripts restore from localStorage after reload; when browser storage is unavailable or full, the UI reports that saving failed.
- The context limit defaults to eight recent user/assistant turns and is capped at 24. Older transcript turns remain in browser storage but are excluded from model requests. This is temporary conversation history, not model long-term memory.
- PDF, DOCX, TXT, and CSV uploads require sign-in, then are extracted and chunked in backend RAM for lexical retrieval. The backend returns relevant file names as chat sources. Uploads are never executed; document bytes/text are not persisted to a database or disk.
- PNG, JPEG, and WebP image attachments are sent transiently to an Ollama model only when its metadata reports `vision`. Text-only models are rejected with a clear error. Images/previews are not restored after refresh.

### Advanced AI capabilities

These capabilities are modular, opt-in, and reported in the UI when external or consequential:

- **Native tool/function calling** through Ollama and OpenAI-compatible providers (`deepseek`, `mistral`, `custom`). Tools are enabled per request from the Toolkit panel; tools are never invoked without the user turning them on. Bedrock remains text-only for native tool calling.
- **Web search** through an operator-configured adapter: Brave Search API, Tavily, or a self-hosted SearXNG JSON endpoint. Search results are real network responses; the model receives titles, snippets, and URLs and is instructed to cite them. The response UI marks responses that used external search and lists their sources.
- **Calculator** using a strict allow-listed AST evaluator. It supports arithmetic, `pi`/`e`/`tau`, and a fixed function set. It never calls `eval`, imports modules, or touches the filesystem/network.
- **Code generation** through the selected model. Generated code is returned as text; it is not executed by the generation route.
- **Sandboxed code execution** only through Docker or Podman: `--network none`, read-only root, `--tmpfs /tmp`, non-root user, `cap-drop ALL`, `no-new-privileges`, and bounded CPU, memory, PIDs, output, and time. There is deliberately **no host subprocess fallback**. Consequential `run_code` calls pause the SSE stream and require explicit approval.
- **CSV analysis** from the deterministic backend parser: inferred types, missing values, distributions, correlations, preview rows, and insights. The parsed table is retained beside the bounded document chunks for in-conversation analysis; formulas are never evaluated.
- **Structured JSON output**: provider JSON modes where available, followed by backend validation against the supplied JSON Schema and at most one repair attempt. Invalid output is rejected, not silently presented as valid.
- **Response regeneration, copy, and Markdown export** per response and for the whole conversation.
- **Voice input and speech output**: browser Speech Recognition/Speech Synthesis by default, or backend-configured OpenAI-compatible STT/TTS endpoints (for example a local whisper.cpp server) selected with `VOICE_*` settings. Microphone audio is sent only to the configured backend speech engine.

## Architecture and request flow

```text
Browser / Next.js
  ├─ GET NEXT_PUBLIC_API_BASE_URL/api/models
  │    └─ Normalized model and provider catalog
  └─ POST NEXT_PUBLIC_API_BASE_URL/api/chat/stream
       └─ FastAPI validates request and resolves provider:model ID
            └─ ProviderRegistry
                 ├─ OllamaProvider ───────────────> local Ollama
                 ├─ DeepSeekProvider ─────────────> DeepSeek Chat Completions API
                 ├─ BedrockProvider ──────────────> AWS Bedrock ConverseStream
                 ├─ MistralProvider ──────────────> Mistral OpenAI-compatible endpoint
                 └─ CustomProvider ───────────────> user's OpenAI-compatible server
```

1. On page load, the frontend calls `GET /api/models`. Ollama's installed model list is discovered from the local Ollama server. The optional cloud/custom catalogs come from backend configuration; the catalog does not make cloud inference calls.
2. Each model has a stable ID in `provider:model` form, such as `ollama:llama3.2:latest` or `deepseek:deepseek-flash`. The backend registry resolves the provider prefix and verifies that the model is available before generation.
3. The browser stores transcripts in `localStorage` and sends the selected model ID, conversation ID, configured context limit, and recent ordered conversation messages. The backend validates the request and retrieves relevant chunks for that conversation from temporary process memory.
4. The selected adapter converts the common chat messages into that provider's own request format. Do not assume every provider's API, authentication, error format, or stream format is identical.
5. Adapters normalize provider output to V-Sutra events. Plain text becomes `token` events; provider-native tool calls are collected and passed to the backend tool loop. FastAPI then emits `notice`, `tool_request`, `tool_confirmation`, `tool_result`, `token`, `done`, or `error` events.
6. When tools are enabled, the backend runs a bounded agent loop (four rounds by default). It sends tool schemas to the provider, executes only enabled tools, appends results as tool messages, and asks the model for a final answer. A `run_code` request pauses the stream until the browser approves or denies it through the confirmation endpoint. Tool failures are returned to the model as tool output and surfaced in the UI; they are never invented or hidden.
7. The frontend appends each token to the assistant message and displays retrieved source file names, tool activity, and external search sources. Pressing Stop aborts the browser request; the backend/provider stream is closed or cancelled where the provider SDK supports it.

Document extraction uses bounded upload sizes, chunk sizes, file counts, and process memory. The in-memory document cache expires after four hours without activity and is lost when FastAPI restarts. Browser localStorage preserves transcript text and attachment names/metadata, not uploaded document contents. After the cache expires or the backend restarts, re-upload documents to ask questions about them again. Image data is never retained for later turns; attach the image again after a refresh.

## Model research and customization

The separate [research workspace](research/README.md) prepares for future customization without changing the running app. It contains instruction and evaluation datasets, offline validation, real endpoint evaluation and benchmarking, hardware planning, versioned model manifests, LoRA/QLoRA templates, and inference compatibility helpers. Training has not been implemented or started. See [hardware requirements](research/docs/hardware-requirements.md) before using any planning template; all hardware figures remain provisional until measured on the target system.

## Repository map

```text
myAI/
├─ README.md                         # Product vision, architecture, decisions, and runbook
├─ frontend/
│  ├─ src/app/                       # Next.js routes, layout, global styles
│  ├─ src/components/NexaApp.tsx     # Chat/model-selection orchestration, tools, voice, export
│  ├─ src/components/                # Presentation components (chat, settings, toolkit, confirmations)
│  ├─ src/lib/chat-stream.ts         # API catalog client and normalized SSE reader
│  ├─ src/lib/types.ts               # Frontend chat, model, provider, tool, and voice types
│  ├─ src/lib/export.ts              # Clipboard and Markdown export helpers
│  ├─ src/lib/speech.ts              # Browser speech recognition/synthesis helpers
│  ├─ src/lib/conversation-store.ts  # Temporary localStorage conversation persistence
│  └─ .env.example                   # Public backend base URL only
├─ backend/
│  ├─ app/main.py                    # FastAPI routes, SSE normalization, context checks, tool confirmation route
│  ├─ app/documents.py               # Temporary document extraction, chunking, retrieval, CSV retention, expiry
│  ├─ app/schemas.py                 # Validated API and catalog schemas
│  ├─ app/config.py                  # Backend environment settings
│  ├─ app/registry.py                # Provider registry, catalog, and model resolution
│  ├─ app/tool_runtime.py            # Bounded provider/tool loop and SSE event payloads
│  ├─ app/confirmations.py           # In-memory approval broker for consequential tool calls
│  ├─ app/structured.py              # JSON extraction, schema validation, and repair
│  ├─ app/voice.py                   # Configurable OpenAI-compatible STT/TTS proxying
│  ├─ app/tools/base.py              # Tool spec, context, result, and error types
│  ├─ app/tools/registry.py          # Tool schemas, dispatch, and availability
│  ├─ app/tools/calculator.py        # Allow-listed AST arithmetic evaluator
│  ├─ app/tools/csv_analysis.py      # Deterministic CSV parsing and statistics
│  ├─ app/tools/sandbox.py           # Docker/Podman-only isolated execution
│  ├─ app/tools/web_search.py        # Brave, Tavily, and SearXNG adapters
│  ├─ app/providers/base.py          # Provider contract, tool events, and normalized errors
│  ├─ app/providers/ollama.py        # Local Ollama adapter, native tools, structured output
│  ├─ app/providers/deepseek.py      # DeepSeek adapter
│  ├─ app/providers/bedrock.py       # AWS Bedrock Converse adapter
│  ├─ app/providers/mistral.py       # Mistral adapter
│  ├─ app/providers/custom.py        # User-hosted OpenAI-compatible adapter
│  ├─ app/providers/openai_compatible.py # Shared protocol mechanics, tool streaming, JSON mode
│  ├─ requirements.txt               # Python dependencies
│  └─ .env.example                   # Backend-only provider, tool, search, sandbox, and voice settings
└─ models/                           # Model notes/profiles; model weights are not committed
```

`Nexa` remains in a few internal frontend filenames from the early working name. The product shown in the UI and documentation is V-Sutra; avoid broad renames unless doing focused cleanup.

## Provider and model management

`GET /api/models` returns:

- `models`: model ID, provider ID, display name, availability, whether configuration is required, context window when known, capabilities, and optional Ollama size/modified time.
- `providers`: provider ID/name, whether it is configured and available, and an optional configuration hint.
- `default_model`: the configured preferred Ollama model if installed; otherwise `null`.

The registry never falls back to a configured cloud model as the default. The user must explicitly pick a cloud model from the selector. An unconfigured provider's configured catalog entries are shown disabled. Bedrock has no model entry until a model ID is supplied in `BEDROCK_MODELS`.

| Provider | Backend configuration | Provider-specific behavior |
| --- | --- | --- |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Calls `/api/tags`, and `/api/show` per installed model for metadata. Uses Ollama `/api/chat` JSON/NDJSON protocol. Needs no cloud credentials. |
| DeepSeek | `DEEPSEEK_API_KEY`, optional `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODELS` | Uses the DeepSeek Chat Completions route and parses its SSE chunks. The default catalog names can be changed in backend config. |
| Amazon Bedrock | `BEDROCK_REGION`, explicit `BEDROCK_MODELS`, AWS SDK credentials | Uses boto3 `converse` and `converse_stream`, not an OpenAI-compatible API. Requires Bedrock model access/permissions in the selected region. |
| Mistral | `MISTRAL_API_KEY`, optional `MISTRAL_BASE_URL`, `MISTRAL_MODELS` | Uses Mistral's documented OpenAI-compatible chat-completions route and normalizes SSE. The provider documents this compatibility endpoint as preview. |
| Custom infrastructure | `CUSTOM_MODEL_BASE_URL`, `CUSTOM_MODEL_ID`, optional name/API key | Expects an OpenAI-compatible server whose base URL ends with `/v1`; the adapter requests `/chat/completions`. Private no-auth endpoints are supported. For a different wire protocol, add a separate adapter implementing `ModelProvider`. |

Availability means the adapter has the required local configuration and, for Ollama, can discover the installed model. DeepSeek and Mistral use configured model IDs and a configured API key; the actual model/account access is confirmed when the selected model is invoked. Bedrock uses the configured region, model IDs, detected local credential configuration, then lets the SDK/provider verify account access at invocation. Cataloging models does not invoke inference or create cloud resources.

Context metadata is omitted when unknown. Ollama supplies context length and capabilities via `/api/show`. `CUSTOM_MODEL_CONTEXT_WINDOW` and `CUSTOM_MODEL_CAPABILITIES` describe a custom endpoint. Optional `*_MODEL_CONTEXT_WINDOWS` settings accept comma-separated `model-id=tokens` values for DeepSeek, Mistral, or Bedrock. Do not guess metadata that the adapter or operator has not supplied.

DeepSeek's Chat Completions streaming is documented in its [official API reference](https://api-docs.deepseek.com/api/create-chat-completion/). AWS Bedrock uses its distinct [ConverseStream API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html). Mistral documents its OpenAI-compatible route and preview status in its [chat completion guide](https://docs.mistral.ai/inference/conversations/chat-completion).

## Settings and device layouts

The Settings panel controls the features that are implemented in the UI:

- **Theme:** dark, light, or follow the operating-system theme.
- **Text size:** small, default, or large for chat messages.
- **Response style:** concise, balanced, or detailed. The frontend sends the selected style as a system instruction for each new generation.
- **Active model:** selects any available catalog model and shows provider readiness. Provider secrets are never entered or shown in the browser; configure them on the backend.
- **Context limit:** selects the number of recent user/assistant turns sent to the model (2, 4, 8, 12, or 20). The API accepts limits up to 24. A bounded context prevents unending prompt growth; it does not delete older transcript history.
- **Clear current conversation:** clears the current conversation transcript and removes its temporary server-side document cache.
- **Clear all temporary history:** removes every saved conversation from browser storage and clears the current in-memory document cache for those conversations.

Theme, text size, and response style persist under `v-sutra-preferences`; conversation transcripts, titles, timestamps, and document metadata persist under `v-sutra-conversations-v1`. Image bytes, extracted document text, provider credentials, and API keys are not saved there. Browser storage can be disabled or quota-limited; the UI surfaces this and the current page can continue temporarily without persistence. This transcript is temporary browser history only: it is not written into a model's long-term memory. The frontend sends only recent turns within the selected context limit on each request.

The shell uses a desktop sidebar, a narrower tablet layout, and a mobile navigation drawer below 720px. On phones, the sidebar opens over a dimmed backdrop, the Settings panel becomes a bottom sheet, composer and header spacing tighten, and safe-area insets are applied. Motion respects `prefers-reduced-motion`. When changing breakpoints or panels, preserve these behaviors and avoid horizontal overflow on narrow screens.

### Adapter contract for future providers

`ModelProvider` is the backend seam. An adapter identifies itself with `provider_id` and `display_name`, reports `configured` and a safe `configuration_hint`, and implements:

- `list_models()`: return normalized `ModelInfo` values. Use stable IDs beginning with the adapter's provider ID, such as `provider:model-name`.
- `complete(messages, model)`: translate validated messages to the provider's own request format and return assistant text.
- `stream_chat(messages, model)`: translate the request and yield plain text fragments, regardless of the source provider's streaming format.
- `stream_chat_events(messages, model, tools)`: optional provider-neutral tool stream. The default implementation yields text only. Ollama and OpenAI-compatible adapters emit normalized `ToolCall` values; set `supports_tools = True` only when the adapter actually implements this path.
- `complete_structured(messages, model, schema)`: optional structured-output path. The base implementation falls back to `complete()`; adapters may use native JSON modes (Ollama `format`, OpenAI-compatible `response_format`). The shared `app/structured.py` validates the returned JSON against the caller's schema.

Raise `ProviderError` with a user-safe message and status code for expected failures. Never include API keys, authorization headers, raw credential-bearing provider responses, or stack traces in normalized responses. Register new adapters in `backend/app/registry.py`; keep frontend code unaware of provider-specific payloads. For a custom server with a different protocol, implement a distinct adapter instead of forcing it through `openai_compatible.py`.

## API contract

### `GET /health`

Returns overall status, provider configuration/availability, and the local default model ID.

### `GET /api/models`

Returns the normalized provider/model catalog described above.

### `POST /api/chat`

Returns a single non-streaming response. The request can include optional `conversation_id`, `context_turn_limit` (1–24), and `enabled_tools`; messages may include transient base64 images for vision-capable local Ollama models. Example:

```json
{
  "model": "ollama:llama3.2:latest",
  "conversation_id": "browser-generated-conversation-id",
  "context_turn_limit": 8,
  "enabled_tools": ["calculator", "analyze_csv"],
  "messages": [
    { "role": "user", "content": "Hello" }
  ]
}
```

Because sandbox execution requires interactive approval, `run_code` is rejected on this non-streaming route with a clear error. Use `/api/chat/stream` for sandboxed execution.

### `POST /api/chat/stream`

Accepts the same request and returns `text/event-stream`. Events are JSON data records:

```text
data: {"type":"notice","message":"Web search is not configured on the backend."}

data: {"type":"tool_request","call_id":"call_1","tool":"calculator","arguments":{"expression":"6*7"}}

data: {"type":"tool_result","call_id":"call_1","tool":"calculator","status":"ok","summary":"6*7 = 42","data":{"value":42},"sources":[]}

data: {"type":"tool_confirmation","confirmation_id":"...","call_id":"call_2","tool":"run_code","arguments":{"language":"python","code":"print(1)"},"expires_in":120}

data: {"type":"token","token":"The answer is "}

data: {"type":"done","model":"ollama:llama3.2:latest","provider":"ollama","sources":[],"search_sources":[],"tools_used":[{"name":"calculator","status":"ok","summary":"6*7 = 42"}]}
```

Provider failures after response streaming begins use `{ "type": "error", "error": "..." }`. Errors before streaming begins use an HTTP error with a safe `detail`. Internal credentials, provider response bodies, and stack traces must not be sent to the browser.

`run_code` calls pause the stream at `tool_confirmation` until the client calls `POST /api/tool-confirmations/{confirmation_id}` with `{ "approved": true|false }`. If the user denies or the request expires, the model receives a tool result explaining that the code did not run. Approvals are process-local, single-use, and disappear on backend restart.

Send recent user and assistant messages in order on every turn to preserve context. The browser retains the transcript; the backend does not store chat history. The backend caps context independently and uses `conversation_id` to associate temporary uploaded documents.

### Tool, structured output, and voice endpoints

- `GET /api/tools` returns tool metadata, availability reasons, search-provider status, sandbox status, and limits.
- `POST /api/tools/calculator` accepts `{ "expression": "..." }` and returns the evaluated result.
- `POST /api/tools/web-search` accepts `{ "query": "...", "max_results": 1-10 }` and returns real provider results.
- `POST /api/tools/csv/analyze` accepts a multipart `file` and returns deterministic CSV analysis and a text summary.
- `POST /api/tools/code/generate` accepts `{ "prompt", "language", "model", "requirements" }` and returns generated code plus the model explanation.
- `POST /api/tools/sandbox/run` accepts `{ "language", "code", "stdin", "confirmed": true }` and returns bounded container output. Requests without `confirmed: true` are rejected. If no container runtime/image is available, it returns a safe unavailable error and never runs code on the host.
- `POST /api/tools/structured` accepts `{ "prompt", "schema", "model", "system" }` and returns validated JSON or a schema-validation error.
- `GET /api/voice/status` returns the configured STT/TTS engines and limits.
- `POST /api/voice/transcribe` accepts multipart audio and returns transcript text when a backend STT engine is configured.
- `POST /api/voice/speech` accepts `{ "text": "..." }` and returns audio when a backend TTS engine is configured.
- `POST /api/tool-confirmations/{confirmation_id}` resolves a pending sandbox approval with `{ "approved": boolean }`.

### Temporary document endpoints

- `POST /api/conversations/{conversation_id}/documents` accepts multipart `files` (PDF, DOCX, TXT, CSV), validates and extracts them, and returns metadata. Extraction/chunks remain in memory only.
- `GET /api/conversations/{conversation_id}/documents` lists unexpired uploaded file metadata.
- `DELETE /api/conversations/{conversation_id}/documents` clears a conversation's temporary documents.
- `DELETE /api/conversations/{conversation_id}/documents/{document_id}` removes one file.
- `DELETE /api/documents/temporary` clears the entire volatile document cache; Settings uses it when clearing all temporary history.

Document limits: 15 MB per file, 40 MB per upload request, eight files per conversation, and bounded extracted/process memory. Image limits: up to four PNG/JPEG/WebP images per message, 8 MB each, and vision-capable local Ollama models only. Unsupported/corrupted inputs return safe client errors. Uploaded content is treated as untrusted reference material and is never executed.

## Configuration and security

Copy the templates from the repository root:

```powershell
Copy-Item frontend/.env.example frontend/.env.local
Copy-Item backend/.env.example backend/.env
```

`frontend/.env.local` contains only `NEXT_PUBLIC_API_BASE_URL`. Public-prefixed Next.js variables are visible to the browser and must never contain provider keys. All provider keys, AWS configuration, model IDs, Ollama connection settings, search credentials, sandbox limits, and voice-engine credentials belong in `backend/.env` or a backend secret manager. Local mode works with all optional cloud, search, and voice settings blank.

### Tool and search configuration

Tools are disabled unless the user enables them in the Toolkit panel. Web search requires one provider:

| Setting | Purpose |
| --- | --- |
| `SEARCH_PROVIDER` | `brave`, `tavily`, `searxng`, or `none` (default). |
| `BRAVE_SEARCH_API_KEY` | Brave Search API subscription token. |
| `TAVILY_API_KEY` | Tavily API key. |
| `SEARXNG_BASE_URL` | Self-hosted SearXNG base URL with JSON output enabled, for example `http://127.0.0.1:8888`. |
| `SEARCH_MAX_RESULTS`, `SEARCH_TIMEOUT_SECONDS` | Bounds for result count and provider timeout. |
| `TOOLS_MAX_ROUNDS`, `TOOL_CONFIRMATION_TIMEOUT_SECONDS`, `TOOL_MAX_RESULT_CHARS` | Agent-loop and tool-output bounds. |

### Sandbox configuration

Sandbox execution is enabled by default only when Docker or Podman is installed. There is no host execution fallback.

| Setting | Purpose |
| --- | --- |
| `SANDBOX_ENABLED` | Set `false` to disable the sandbox entirely. |
| `SANDBOX_ENGINE` | `auto` (default), `docker`, or `podman`. |
| `SANDBOX_TIMEOUT_SECONDS`, `SANDBOX_MEMORY_MB`, `SANDBOX_CPUS`, `SANDBOX_PIDS_LIMIT`, `SANDBOX_MAX_OUTPUT_BYTES` | Container limits. |
| `SANDBOX_MAX_CODE_CHARS`, `SANDBOX_MAX_STDIN_CHARS` | Source/input size limits. |
| `SANDBOX_PYTHON_IMAGE`, `SANDBOX_JAVASCRIPT_IMAGE` | Locally available images. Pull them before use. |

Pull images explicitly, for example `docker pull python:3.12-alpine` and `docker pull node:22-alpine`. The code container runs with no network, a read-only root filesystem, a writable bounded `/tmp`, a non-root user, all capabilities dropped, and `no-new-privileges`.

### Voice configuration

The browser provides speech recognition and speech synthesis with no credentials. To use backend engines, configure:

| Setting | Purpose |
| --- | --- |
| `VOICE_STT_PROVIDER` | `none` (browser recognition), `whisper_compatible` (local server), or `openai`. |
| `VOICE_STT_BASE_URL`, `VOICE_STT_API_KEY`, `VOICE_STT_MODEL` | OpenAI-compatible `/audio/transcriptions` endpoint. |
| `VOICE_TTS_PROVIDER` | `browser` (default), `openai`, or `openai_compatible`. |
| `VOICE_TTS_BASE_URL`, `VOICE_TTS_API_KEY`, `VOICE_TTS_MODEL`, `VOICE_TTS_VOICE`, `VOICE_TTS_FORMAT` | OpenAI-compatible `/audio/speech` endpoint. |
| `VOICE_MAX_UPLOAD_MB`, `VOICE_MAX_TEXT_CHARS`, `VOICE_STT_TIMEOUT_SECONDS` | Upload/synthesis bounds. |

Browser speech recognition depends on browser support and usually requires HTTPS or localhost. Microphone permission is requested by the browser; audio is sent only to the selected speech engine.

Do not commit `.env`/`.env.local`, credentials, cloud model weights, or Ollama caches. The API has a cookie-based account flow for gated features; guest text chat remains available. For single-machine use, bind it to localhost. Before public exposure, review the deployment security limits, configure a durable account database, and restrict Ollama to trusted hosts. `FRONTEND_ORIGINS` is a comma-separated CORS allowlist, not authentication.

No cloud service is provisioned by this application. Do not add code that automatically deploys or activates a paid service. Keep remote model inference behind explicit model selection and user send action.

## Run locally on Windows PowerShell

Prerequisites: Node.js 20+, Python 3.10+, Ollama installed, and a model pulled locally. Use separate terminals and run commands from the repository root unless the command changes directory. Docker or Podman is optional and is needed only for sandboxed code execution; pull `python:3.12-alpine` and/or `node:22-alpine` to use both sandbox languages.

### Terminal 1: Ollama

```powershell
ollama serve
```

If needed, pull a local model in another terminal:

```powershell
ollama pull llama3.2
```

### Terminal 2: FastAPI backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

For an existing backend virtual environment, skip venv creation and activation. API docs: `http://localhost:8000/docs`.

### Terminal 3: Next.js frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. After changing `.env.local`, restart Next.js.

## Verification and observed status

Useful checks:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/api/models
Invoke-RestMethod http://localhost:8000/api/tools
```

Observed status in this workspace:

- Frontend `npm test` passes 4 SSE framing/cancellation cases, `npx tsc --noEmit` passes, `npm run lint` passes, and `npm run build` passes after the engineering audit changes.
- Backend `python -m unittest discover -s tests -v` passes 13 tests covering calculator safety, CSV statistics, request validation, confirmation gating, exact CORS origins, upload/decompression limits, throttling, and chat concurrency.
- Backend tests emit a Starlette deprecation warning because the installed TestClient currently uses HTTPX; the tests pass, but the warning should be resolved during dependency maintenance.
- The API now applies per-process sliding-window request limits, a 128 MB request body limit, and a default cap of two simultaneous model inferences across chat, code generation, and structured output. These controls do not coordinate across workers.
- Dialog focus is trapped and restored for keyboard users; reduced-motion preferences are respected. Tool/voice status fetch failures are surfaced rather than remaining in a loading state.
- Runtime probe on this machine: Ollama reports `llama3.2:latest` loaded as `100% CPU`, context 4096, runner size 2.6 GB. A tiny three-token probe took 44.3 seconds cold (33.3 seconds reported model load) and 2.16 seconds warm (77 ms reported load); this is diagnostic only, not a throughput benchmark.
- `nvidia-smi`, Docker, and Podman were not found. GPU utilization/VRAM and real sandbox isolation could not be measured. WMI hardware inventory was denied, so host RAM and GPU model are unknown. No reliable memory capacity or concurrency benchmark was produced.
- **This is not a production-readiness claim.** There is no verified GPU utilization/VRAM data, repeatable model performance benchmark, or multi-worker capacity result. The tiny local probe above is not representative. Local checks do not prove live provider integrations, browser microphone behavior, or real container isolation.
- The calculator rejects non-arithmetic expressions; CSV analysis returns real computed statistics; structured output validates against the supplied schema.
- **Docker/Podman is not installed in this verification environment**, so real sandbox execution was not exercised. The API correctly reports the sandbox as unavailable and refuses to run code on the host. Install a container runtime and pull the configured images to exercise it end to end.
- Cloud adapters and external search/voice providers were not exercised with paid credentials. They require provider-specific account/model/API access to verify end to end. A vision model must be installed locally to exercise actual image understanding; text-only models are intentionally rejected for image requests. Browser speech features depend on browser support and microphone permissions.

## Guidance for the next AI or developer

1. Read this README and the relevant source files before editing. Treat the vision and safety constraints above as active requirements.
2. Keep frontend types and UI provider-agnostic. Add provider-specific wire formats, authentication, parsing, and error mapping under `backend/app/providers/`.
3. Implement the `ModelProvider` contract and register the adapter in `backend/app/registry.py`. Keep catalog metadata truthful and omit unknown context sizes/capabilities.
4. Preserve normalized IDs, chat request/response shapes, and `token`/`done`/`error` SSE events unless both API and frontend are intentionally updated together.
5. Never make cloud the default, place credentials in frontend code, make mock output look real, add a database/authentication, or provision paid services without a new explicit requirement. Temporary browser transcript storage and volatile document processing are part of the current product scope.
6. Update this README when architecture, provider behavior, configuration, run commands, verification, or product scope changes.

## Current scope boundaries

- No durable transcript sync, durable document storage, billing UI, or model long-term memory. Account/session metadata is stored in local SQLite or optional private Supabase Postgres; transcript history is temporary browser localStorage, document processing is volatile backend memory, and image bytes are request-only.
- No automatic cloud provisioning or inference, and no automatic external search. External search and cloud/voice engines are callable only when configured on the backend and explicitly selected/enabled by the user.
- No host execution of generated code under any circumstances. Sandboxed execution requires a local Docker/Podman runtime; without one the feature is unavailable.
- No universal assumption that providers share the same API: Bedrock uses Converse, Ollama uses its own chat protocol, and DeepSeek/Mistral/custom adapters own their individual request and stream mapping. Bedrock does not currently participate in native tool calling.
- Cloud credentials, search API keys, and provider account access are not included in this repository and must be configured by the operator on the backend host.
