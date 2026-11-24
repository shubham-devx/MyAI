# Local development

Use three terminals from the repository root.

## Start services

Terminal 1 (optional model service):

```powershell
ollama serve
```

Terminal 2 (FastAPI):

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 3 (Next.js):

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000`. The API health and model/tool status endpoints are `/health`, `/api/models`, and `/api/tools`; interactive API documentation is at `/docs`.

Plain text chat works while signed out. Selecting uploads, AI tools, voice features, response export/copy, or settings opens the account dialog. Sign-up and sign-in use SQLite locally at `backend/data/auth.sqlite3` by default. Set `AUTH_DATABASE_URL` to use a PostgreSQL connection instead.

Password-reset links are only delivered when Brevo is configured. In `backend/.env`, set `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` to an address verified by Brevo, optional `BREVO_SENDER_NAME`, and `APP_WEB_URL=http://localhost:3000`. Restart FastAPI after changing environment variables. Until configured, the Forgot password screen explains that email recovery is not enabled; signup and login still work.

## Automated checks

From `backend/`:

```powershell
python -m unittest discover -s tests -v
```

From `frontend/`:

```powershell
npm test
npm run lint
npm run build
```

Backend tests use the standard-library `unittest` runner and FastAPI's installed test client. Frontend streaming parser tests use Node's built-in test runner. These checks do not validate live cloud credentials, browser microphone permissions, model quality, GPU throughput, or real container isolation.

## Data and logs

Conversation transcripts are browser-local temporary data and are not synced between accounts. Account credentials, hashed session tokens, password-reset tokens, and conversation ownership metadata are stored in SQLite locally or in Supabase Postgres when `AUTH_DATABASE_URL` is configured. Passwords are PBKDF2-HMAC-SHA256 hashed; session and reset tokens are random and only their hashes are stored. Uploaded documents and extracted content remain in backend process memory only and expire after inactivity; a backend restart clears them. Protect database credentials and backups, and do not add private data or credentials to logs, test fixtures, or commits.
