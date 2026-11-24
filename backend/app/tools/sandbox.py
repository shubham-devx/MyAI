"""Strict container sandbox for generated code.

Security properties:
- Only a container runtime (Docker or Podman) is used. There is deliberately no local
  subprocess fallback, so arbitrary code is never executed directly on the host.
- No network namespace, no host mounts, read-only root filesystem, no Linux capabilities,
  no privilege escalation, non-root user, bounded CPU/memory/PIDs/output/time.
"""

import asyncio
import base64
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.tools.base import ToolError

_PYTHON_RUNNER = """import base64, json, os, sys
payload = json.load(sys.stdin)
with open("main.py", "wb") as handle:
    handle.write(base64.b64decode(payload.get("code", "")))
with open("stdin.txt", "wb") as handle:
    handle.write(base64.b64decode(payload.get("stdin", "")))
descriptor = os.open("stdin.txt", os.O_RDONLY)
os.dup2(descriptor, 0)
os.execv(sys.executable, [sys.executable, "main.py"])
"""

_JAVASCRIPT_RUNNER = """const fs = require('fs');
let raw = '';
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  const payload = JSON.parse(raw);
  fs.writeFileSync('main.js', Buffer.from(payload.code || '', 'base64'));
  fs.writeFileSync('stdin.txt', Buffer.from(payload.stdin || '', 'base64'));
  const descriptor = fs.openSync('stdin.txt', 'r');
  const result = require('child_process').spawnSync(process.execPath, ['main.js'], { stdio: [descriptor, 1, 2] });
  process.exit(result.status === null ? 1 : result.status);
});
"""

_LANGUAGES = {
    "python": {
        "label": "Python",
        "image": lambda: settings.sandbox_python_image,
        "command": ["python", "-c", _PYTHON_RUNNER],
    },
    "javascript": {
        "label": "JavaScript",
        "image": lambda: settings.sandbox_javascript_image,
        "command": ["node", "-e", _JAVASCRIPT_RUNNER],
    },
}


@dataclass
class SandboxExecution:
    ok: bool
    language: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool
    engine: str
    image: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "language": self.language,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "engine": self.engine,
            "image": self.image,
        }

    def content_for_model(self) -> str:
        lines = [
            f"Sandbox execution ({self.language}, {self.engine}, image {self.image})",
            f"Exit code: {self.exit_code if self.exit_code is not None else 'none'}",
        ]
        if self.timed_out:
            lines.append(f"Timed out after {settings.sandbox_timeout_seconds:g} seconds.")
        if self.truncated:
            lines.append("Output was truncated at the configured limit.")
        lines.append("STDOUT:")
        lines.append(self.stdout or "(empty)")
        lines.append("STDERR:")
        lines.append(self.stderr or "(empty)")
        return "\n".join(lines)


def _engine_candidates() -> list[str]:
    choice = (settings.sandbox_engine or "auto").strip().lower()
    if choice in {"docker", "podman"}:
        return [choice]
    return ["docker", "podman"]


def find_engine() -> str | None:
    for candidate in _engine_candidates():
        if shutil.which(candidate):
            return candidate
    return None


def _engine_hint() -> str:
    choice = (settings.sandbox_engine or "auto").strip().lower()
    requested = choice if choice in {"docker", "podman"} else "Docker or Podman"
    return (
        f"{requested} was not found. Install a container runtime to enable sandboxed execution. "
        "V-Sutra never falls back to running generated code directly on the host."
    )


async def _inspect_image(engine: str, image: str) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            engine, "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await asyncio.wait_for(process.wait(), timeout=10)
    except (OSError, asyncio.TimeoutError):
        return False


async def _engine_ready(engine: str) -> bool:
    """Return True only when the container runtime service itself answers."""
    try:
        process = await asyncio.create_subprocess_exec(
            engine, "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await asyncio.wait_for(process.wait(), timeout=15) == 0
    except (OSError, asyncio.TimeoutError):
        return False


async def sandbox_status() -> dict[str, Any]:
    engine = find_engine()
    status: dict[str, Any] = {
        "enabled": settings.sandbox_enabled,
        "engine": engine,
        "available": False,
        "hint": None,
        "languages": [],
        "limits": {
            "timeout_seconds": settings.sandbox_timeout_seconds,
            "memory_mb": settings.sandbox_memory_mb,
            "cpus": settings.sandbox_cpus,
            "pids": settings.sandbox_pids_limit,
            "network": "disabled",
            "filesystem": "read-only root with bounded tmpfs",
            "output_bytes": settings.sandbox_max_output_bytes,
        },
    }
    if not settings.sandbox_enabled:
        status["hint"] = "Sandbox execution is disabled by SANDBOX_ENABLED=false on the backend."
        return status
    if engine is None:
        status["hint"] = _engine_hint()
        return status
    if not await _engine_ready(engine):
        status["hint"] = (
            f"{engine} is installed but its service is not reachable. Start {engine} and try again. "
            "V-Sutra never falls back to running generated code directly on the host."
        )
        return status
    images = list(_LANGUAGES.items())
    readiness = await asyncio.gather(*(_inspect_image(engine, config["image"]()) for _, config in images))
    languages = [
        {"name": name, "label": config["label"], "image": config["image"](), "ready": ready}
        for (name, config), ready in zip(images, readiness)
    ]
    status["languages"] = languages
    status["available"] = any(item["ready"] for item in languages)
    if not status["available"]:
        pulls = ", ".join(item["image"] for item in languages)
        status["hint"] = f"Pull a sandbox image first, for example: {engine} pull {pulls.split(', ')[0]}"
    elif not all(item["ready"] for item in languages):
        missing = [item for item in languages if not item["ready"]]
        status["hint"] = f"Some images are not local yet: {', '.join(item['image'] for item in missing)}"
    return status


def _validate(language: str, code: str, stdin: str) -> tuple[str, str]:
    normalized = language.strip().lower()
    if normalized not in _LANGUAGES:
        raise ToolError("Sandboxed execution supports Python and JavaScript.")
    if not code.strip():
        raise ToolError("Enter code to execute.")
    if len(code) > settings.sandbox_max_code_chars:
        raise ToolError(f"Code is limited to {settings.sandbox_max_code_chars:,} characters.")
    if len(stdin) > settings.sandbox_max_stdin_chars:
        raise ToolError(f"Standard input is limited to {settings.sandbox_max_stdin_chars:,} characters.")
    return normalized, code


def _build_command(engine: str, language: str, image: str, name: str) -> list[str]:
    config = _LANGUAGES[language]
    return [
        engine, "run", "--rm", "--name", name,
        "--network", "none",
        "--ipc", "none",
        "--memory", f"{settings.sandbox_memory_mb}m",
        "--memory-swap", f"{settings.sandbox_memory_mb}m",
        "--cpus", str(settings.sandbox_cpus),
        "--pids-limit", str(settings.sandbox_pids_limit),
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--ulimit", "nofile=64:64",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", "HOME=/tmp",
        "--env", "TMPDIR=/tmp",
        "--workdir", "/tmp",
        "-i",
        image,
        *config["command"],
    ]


async def _collect(stream: asyncio.StreamReader, limit: int, on_overflow) -> tuple[bytes, bool]:
    data = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if len(data) < limit:
            data.extend(chunk[: limit - len(data)])
        if len(data) >= limit:
            truncated = True
            on_overflow()
            break
    return bytes(data), truncated


async def _remove_container(engine: str, name: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            engine, "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=10)
    except (OSError, asyncio.TimeoutError):
        pass


async def run_sandboxed(language: str, code: str, stdin: str = "") -> SandboxExecution:
    if not settings.sandbox_enabled:
        raise ToolError("Sandbox execution is disabled by the backend operator.", 503)
    normalized, source = _validate(language, code, stdin)
    engine = find_engine()
    if engine is None:
        raise ToolError(_engine_hint(), 503)
    if not await _engine_ready(engine):
        raise ToolError(
            f"{engine} is installed but its service is not reachable. Start {engine} and try again. "
            "V-Sutra never falls back to running generated code directly on the host.",
            503,
        )
    image = _LANGUAGES[normalized]["image"]()
    if not await _inspect_image(engine, image):
        raise ToolError(
            f"The sandbox image {image} is not available locally. Pull it first with: {engine} pull {image}",
            503,
        )

    name = f"vsutra-{uuid.uuid4().hex[:12]}"
    command = _build_command(engine, normalized, image, name)
    payload = json.dumps({
        "code": base64.b64encode(source.encode("utf-8")).decode("ascii"),
        "stdin": base64.b64encode(stdin.encode("utf-8")).decode("ascii"),
    }).encode("utf-8")

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise ToolError("The container runtime could not be started. Check that Docker or Podman is installed and running.", 503) from error

    assert process.stdin and process.stdout and process.stderr
    try:
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass

    overflowed = False

    def mark_overflow() -> None:
        nonlocal overflowed
        overflowed = True

    stdout_task = asyncio.create_task(_collect(process.stdout, settings.sandbox_max_output_bytes, mark_overflow))
    stderr_task = asyncio.create_task(_collect(process.stderr, settings.sandbox_max_output_bytes, mark_overflow))
    wait_task = asyncio.create_task(process.wait())
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=settings.sandbox_timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
    if overflowed:
        process.kill()
        if not wait_task.done():
            await process.wait()
    stdout, stdout_truncated = await stdout_task
    stderr, stderr_truncated = await stderr_task
    if timed_out or overflowed:
        await _remove_container(engine, name)

    exit_code = process.returncode
    duration_ms = int((time.monotonic() - started) * 1000)
    execution = SandboxExecution(
        ok=exit_code == 0 and not timed_out,
        language=normalized,
        exit_code=exit_code,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration_ms=duration_ms,
        timed_out=timed_out,
        truncated=stdout_truncated or stderr_truncated or overflowed,
        engine=engine,
        image=image,
    )
    if timed_out:
        execution.stderr = (execution.stderr + f"\nSandbox timed out after {settings.sandbox_timeout_seconds:g}s.").strip()
    return execution
