# Installation

This guide installs V-Sutra for local development on Windows using PowerShell. Text chat is available without an account; uploads and other extended features require sign-in. Account records and sessions use a local SQLite file. Keep the API bound to loopback for personal/local use.

## Prerequisites

- Node.js 20 or newer and npm.
- Python 3.10 or newer.
- Ollama installed and running for local model inference. The UI/API can start without Ollama, but chat generation needs an available model provider.
- Docker or Podman is optional and required only for code execution. The application never runs generated code directly on the host.

## Install

From the repository root, open separate terminals:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
```

The frontend example points to `http://localhost:8000`. Keep provider credentials and private endpoints in `backend/.env`; never put secrets in the frontend environment.

The backend creates `backend/data/auth.sqlite3` for account storage at first startup. Password reset emails are disabled until `BREVO_API_KEY` and a verified `BREVO_SENDER_EMAIL` are configured in `backend/.env`. Set `APP_WEB_URL` to the browser-visible frontend URL used in reset links. Do not commit `.env` files or the database directory.

Start Ollama and pull a local chat model, for example:

```powershell
ollama serve
ollama pull llama3.2
```

Code execution stays unavailable until a container runtime is running and the configured sandbox image has been pulled. See [model configuration](model-configuration.md) and [deployment](deployment.md) for optional integrations and exposure restrictions.
