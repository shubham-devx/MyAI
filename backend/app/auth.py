"""Local account, session, and password reset storage."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from app.config import settings

SESSION_COOKIE = "vs_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
RESET_SECONDS = 30 * 60
PBKDF2_ROUNDS = 600_000


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}:{digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, expected = encoded.split(":", 1)
        return hmac.compare_digest(_password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1], expected)
    except (ValueError, TypeError):
        return False


class AuthStore:
    def __init__(self, path: str, database_url: str | None = None):
        self.database_url = database_url.strip() if database_url and database_url.strip() else None
        self.schema = "vsutra_private" if self.database_url else None
        self.path = Path(path).expanduser().resolve() if not self.database_url else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._connect() as (db, _prefix):
            if self.database_url:
                for statement in (
                    "CREATE SCHEMA IF NOT EXISTS vsutra_private",
                    """CREATE TABLE IF NOT EXISTS vsutra_private.users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                    )""",
                    """CREATE TABLE IF NOT EXISTS vsutra_private.sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES vsutra_private.users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                    )""",
                    "CREATE INDEX IF NOT EXISTS sessions_user_idx ON vsutra_private.sessions(user_id)",
                    """CREATE TABLE IF NOT EXISTS vsutra_private.password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES vsutra_private.users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                    )""",
                    """CREATE TABLE IF NOT EXISTS vsutra_private.conversations (
                    user_id TEXT NOT NULL REFERENCES vsutra_private.users(id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL,
                    PRIMARY KEY (user_id, conversation_id),
                    UNIQUE (conversation_id)
                    )""",
                ):
                    db.execute(statement)
            else:
                db.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
                    CREATE TABLE IF NOT EXISTS password_resets (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS conversations (
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        conversation_id TEXT NOT NULL,
                        PRIMARY KEY (user_id, conversation_id),
                        UNIQUE (conversation_id)
                    );
                """)

    @contextmanager
    def _connect(self):
        if self.database_url:
            import psycopg
            from psycopg.rows import dict_row

            db = psycopg.connect(self.database_url, connect_timeout=10, sslmode="require", row_factory=dict_row)
            prefix = f"{self.schema}."
        else:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            prefix = ""
        try:
            yield db, prefix
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_user(self, email: str, password: str) -> tuple[dict | None, str | None]:
        normalized = email.strip().lower()
        user_id = str(uuid.uuid4())
        with self.lock, self._connect() as (db, prefix):
            inserted = self._execute(db, f"INSERT INTO {prefix}users VALUES (?, ?, ?, ?) ON CONFLICT(email) DO NOTHING", (user_id, normalized, _password_hash(password), int(time.time())))
            if inserted.rowcount == 0:
                return None, None
        return {"id": user_id, "email": normalized}, self.new_session(user_id)

    def login(self, email: str, password: str) -> tuple[dict | None, str | None]:
        with self.lock, self._connect() as (db, prefix):
            row = self._execute(db, f"SELECT * FROM {prefix}users WHERE email=?", (email.strip().lower(),)).fetchone()
            if not row or not _verify_password(password, row["password_hash"]):
                return None, None
            user = {"id": row["id"], "email": row["email"]}
        return user, self.new_session(user["id"])

    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock, self._connect() as (db, prefix):
            self._execute(db, f"DELETE FROM {prefix}sessions WHERE expires_at <= ?", (int(time.time()),))
            self._execute(db, f"INSERT INTO {prefix}sessions VALUES (?, ?, ?)", (_secret_hash(token), user_id, int(time.time()) + SESSION_SECONDS))
        return token

    def get_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.lock, self._connect() as (db, prefix):
            row = self._execute(db, f"SELECT users.id, users.email FROM {prefix}sessions JOIN {prefix}users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?", (_secret_hash(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if token:
            with self.lock, self._connect() as (db, prefix):
                self._execute(db, f"DELETE FROM {prefix}sessions WHERE token_hash=?", (_secret_hash(token),))

    def issue_reset(self, email: str) -> str | None:
        token = secrets.token_urlsafe(32)
        with self.lock, self._connect() as (db, prefix):
            row = self._execute(db, f"SELECT id FROM {prefix}users WHERE email=?", (email.strip().lower(),)).fetchone()
            if not row:
                return None
            self._execute(db, f"DELETE FROM {prefix}password_resets WHERE user_id=?", (row["id"],))
            self._execute(db, f"INSERT INTO {prefix}password_resets VALUES (?, ?, ?)", (_secret_hash(token), row["id"], int(time.time()) + RESET_SECONDS))
        return token

    def reset_password(self, token: str, password: str) -> bool:
        now = int(time.time())
        with self.lock, self._connect() as (db, prefix):
            if not self.database_url:
                db.execute("BEGIN IMMEDIATE")
            lock_clause = " FOR UPDATE" if self.database_url else ""
            row = self._execute(db, f"SELECT user_id FROM {prefix}password_resets WHERE token_hash=? AND expires_at>?{lock_clause}", (_secret_hash(token), now)).fetchone()
            if not row:
                return False
            self._execute(db, f"UPDATE {prefix}users SET password_hash=? WHERE id=?", (_password_hash(password), row["user_id"]))
            self._execute(db, f"DELETE FROM {prefix}password_resets WHERE user_id=?", (row["user_id"],))
            self._execute(db, f"DELETE FROM {prefix}sessions WHERE user_id=?", (row["user_id"],))
            return True

    def owns_conversation(self, user_id: str, conversation_id: str, create: bool = False) -> bool:
        with self.lock, self._connect() as (db, prefix):
            if create:
                inserted = self._execute(db, f"INSERT INTO {prefix}conversations VALUES (?, ?) ON CONFLICT DO NOTHING", (user_id, conversation_id))
                if inserted.rowcount == 1:
                    return True
            row = self._execute(db, f"SELECT 1 FROM {prefix}conversations WHERE user_id=? AND conversation_id=?", (user_id, conversation_id)).fetchone()
            return row is not None

    def conversation_ids(self, user_id: str) -> list[str]:
        with self.lock, self._connect() as (db, prefix):
            return [row["conversation_id"] for row in self._execute(db, f"SELECT conversation_id FROM {prefix}conversations WHERE user_id=?", (user_id,))]

    @staticmethod
    def _execute(db, query: str, params: tuple = ()):
        return db.execute(query.replace("?", "%s"), params) if not isinstance(db, sqlite3.Connection) else db.execute(query, params)


auth_store = AuthStore(settings.auth_database_path, settings.auth_database_url)


async def optional_user(request: Request) -> dict | None:
    return auth_store.get_user(request.cookies.get(SESSION_COOKIE))


async def require_user(user: dict | None = Depends(optional_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to use this feature.")
    return user


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )
