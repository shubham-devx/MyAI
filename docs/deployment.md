# Deployment and exposure boundaries

The application supports local SQLite accounts and per-account authorization for extended features. It is still not a public multi-user production deployment package: conversation transcripts remain browser-local, uploaded documents are temporary per-process memory, rate/concurrency limits are per-process, and there is no email verification, distributed rate limiter, or worker coordination. Keep the API on loopback or behind a trusted private gateway.

## Vercel frontend + Render FastAPI backend

The repository includes a Render Blueprint at [`render.yaml`](../render.yaml). It creates a Free Python FastAPI web service. Supabase Postgres stores account/session data so Render's ephemeral filesystem is not used for durable data. Render Free may sleep when idle, so the first request after a quiet period can be slow.

### 1. Deploy the backend on Render

1. Push the repository to GitHub, GitLab, or Bitbucket, then create a Render Blueprint from the repository root.
2. In Supabase, apply [`supabase/migrations/20261124000000_vsutra_auth.sql`](../supabase/migrations/20261124000000_vsutra_auth.sql) through the SQL Editor. The tables are in the private `vsutra_private` schema; do not expose that schema through the Data API.
3. In Supabase Dashboard → Connect, copy the database connection string. For an IPv4-only route, use the shared pooler's **Session** connection string (port 5432). Keep the database password private.
4. During Render Blueprint setup, set `AUTH_DATABASE_URL` to that connection string, `FRONTEND_ORIGINS` to the exact Vercel origin (for example `https://your-app.vercel.app`), and `APP_WEB_URL` to the canonical Vercel URL. Use no paths or trailing slash.
5. If you rename the Render service, update `TRUSTED_HOSTS` to its exact `onrender.com` hostname. The Render service name in the Blueprint is `vsutra-api-prod`.
6. Configure at least one inference provider in Render's Environment settings. The default Ollama URL points inside the Render service, where no Ollama process is running. Set a cloud provider key/model or configure `CUSTOM_MODEL_BASE_URL` to a reachable inference server. Keep provider secrets on Render.
7. Add `BREVO_API_KEY` and a verified `BREVO_SENDER_EMAIL` on Render when you want password-reset email enabled. Keep these backend-only.
8. Deploy and verify `https://vsutra-api-prod.onrender.com/health`. HTTP 200 confirms the API is responding; inspect its payload to confirm an inference provider is available.

Render's native Python service does not provide a Docker/Podman runtime for the isolated code runner, so the blueprint disables sandbox execution. Keep generated-code execution disabled unless you connect a separately secured container runner. The default Ollama URL also will not provide a model on Render; configure a cloud provider or reachable custom inference server before expecting chat replies.

Render Free services sleep after 15 idle minutes and erase local files on restart/redeploy. Account data remains in Supabase Postgres; uploaded documents and process-memory data are temporary and are cleared when Render restarts. See [Render's free-service limits](https://render.com/docs/free).

### 2. Deploy the frontend on Vercel

1. Import the same repository into Vercel and set Root Directory to `frontend`. Keep the detected Next.js framework and default build settings.
2. Set these Vercel Production environment variables, then redeploy:

   - `API_PROXY_TARGET=https://vsutra-api-prod.onrender.com`
   - `NEXT_PUBLIC_API_BASE_URL=/backend-api`

   `NEXT_PUBLIC_API_BASE_URL` is embedded into the browser bundle at build time. The Next.js rewrite proxies `/backend-api/*` to Render, keeping browser requests and the HttpOnly session cookie on the Vercel origin. See [Vercel's external rewrite documentation](https://vercel.com/docs/routing/rewrites).

3. The exact Vercel origin must match Render's `FRONTEND_ORIGINS`. Configure exact Preview origins or a separate staging backend for Vercel Preview deployments.
4. Verify guest text chat, signup/login/logout, uploads, tools, streaming, and password recovery if Brevo is configured.

The app uses its own account/password flow stored in Supabase Postgres; it does not currently use Supabase Auth. The Supabase database password belongs only in Render's `AUTH_DATABASE_URL`, never in Vercel or browser code.

## Local run

Bind FastAPI to `127.0.0.1`, run Next.js on localhost, configure exact frontend origins, and store secrets only in the backend environment. In-memory limits default to 120 requests/minute per IP, 12 chat requests/minute, 10 upload requests/minute, 10 signup/login/reset attempts per minute, 5 password-reset requests per minute, two active inference requests per process (including chat, code generation, and structured output), and 128 MB per request. Each worker has independent counters; counters also reset when the service restarts. Excess requests receive HTTP 429 with a retry delay; excess inference gets HTTP 503 instead of being queued. A reverse proxy/load balancer must independently enforce body size, timeouts, TLS, connection limits, and shared rate limits before any network exposure.

The API adds browser security headers. In production (`APP_ENV=production`), interactive API documentation and the OpenAPI endpoint are disabled. Set `TRUSTED_HOSTS` to a comma-separated list of exact API hostnames/IPs; do not use `*`. Set `FRONTEND_ORIGINS` to exact browser origins. The frontend disables the `X-Powered-By` header and sets corresponding browser security headers. TLS termination and HSTS belong at the HTTPS ingress.

## Before a network deployment

This code is not sufficient by itself for public deployment. Configure account recovery through Brevo, require verified email addresses before general availability, and review abuse controls before exposing signup. A full network deployment also needs durable per-user data isolation, a shared rate limiter, secret management, TLS, logging/retention policy, model-provider egress controls, and operational monitoring. Do not bypass this boundary by setting CORS to `*`; CORS is a browser policy, not API access control.

## Private deployment checklist

1. Run the API and model service on a private network. Bind Uvicorn to loopback when it is on the same host, or to a private interface only when an authenticated gateway is in front.
2. Set `APP_ENV=production`, exact `TRUSTED_HOSTS`, `FRONTEND_ORIGINS`, and the production `NEXT_PUBLIC_API_BASE_URL` at build time. Keep provider credentials in the deployment secret store.
3. Terminate TLS at a maintained ingress; enforce request body limits, request/read timeouts, connection limits, shared IP throttling, and allowed egress there.
4. Use a single API worker unless distributed limiting and inference concurrency controls are supplied by the gateway. The built-in counters are per-process and volatile.
5. Configure durable log collection and alerts for provider failures, 429/503 responses, memory pressure, disk pressure, and model availability. Avoid logging prompts, uploaded content, or credentials.
6. Run the documented test and build commands against the deployment configuration and verify health, streaming disconnect behavior, upload boundaries, and sandbox availability on the actual host.
7. Configure either `AUTH_DATABASE_URL` for PostgreSQL or `AUTH_DATABASE_PATH` on protected persistent storage for SQLite. Include backups and restore checks. Configure `APP_WEB_URL`, `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, and optional `BREVO_SENDER_NAME` for password recovery. Keep the Brevo key backend-only.

## Resource and model operations

The model server (Ollama or another provider) is an independent service. V-Sutra does not manage GPU allocation, model loading/unloading, or autoscaling. Its per-process concurrency cap is not a distributed scheduler. Check model server health, GPU memory, host memory, disk space, queue depth, cold-start latency, and token throughput under the expected load before planning capacity. The app has no validated capacity figures; capture real measurements on the target hardware.

Code execution requires Docker/Podman and runs with `--network none`, a read-only root, bounded writable temporary storage, a non-root user, dropped capabilities, and resource limits. Container hardening still depends on keeping the host runtime and kernel patched. Keep the API local unless the broader network security design is in place.

Do not describe the deployment as production-ready based solely on a successful local build or unit test run.
