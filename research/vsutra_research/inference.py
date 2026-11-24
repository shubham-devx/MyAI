"""Inference compatibility layer for evaluating and deploying a future custom model.

The layer speaks three protocols without changing the main application:

- ``openai``: an OpenAI-compatible ``/chat/completions`` server such as vLLM, TGI, SGLang,
  llama.cpp server, or LM Studio.
- ``ollama``: a local Ollama ``/api/chat`` server.
- ``vsutra``: the V-Sutra backend's own ``/api/chat/stream`` endpoint.

No code in this module trains, quantizes, downloads, or serves a model. It only sends
requests to a server the operator runs and measures what that server actually returns.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

ALLOWED_CAPABILITIES = {"chat", "streaming", "tools", "vision"}
_OPENAI_CHAT_PATH = "/chat/completions"


class InferenceError(RuntimeError):
    """A user-safe inference failure that does not include credentials or response bodies."""


@dataclass
class ChatResult:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    ttft_ms: float | None = None
    latency_ms: float = 0.0
    finish_reason: str | None = None
    notices: list[str] = field(default_factory=list)

    @property
    def approx_output_tokens(self) -> int:
        if isinstance(self.usage.get("completion_tokens"), int):
            return int(self.usage["completion_tokens"])
        return len(self.text.split())


def _payload_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise InferenceError(f"Unsupported message role: {role!r}")
        item: dict[str, Any] = {"role": role, "content": message.get("content", "")}
        if role == "assistant" and message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        if role == "tool":
            item["tool_call_id"] = message.get("tool_call_id", "")
            if message.get("name"):
                item["name"] = message["name"]
        payload.append(item)
    return payload


def _api_key(explicit: str | None, env_name: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    return None


class OpenAIBackend:
    """OpenAI-compatible chat completions with streaming."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._api_key = _api_key(api_key, api_key_env)

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        seed: int | None = None,
    ) -> ChatResult:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover
            raise InferenceError("httpx is required for inference: pip install httpx") from error

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _payload_messages(messages),
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = seed

        started = time.perf_counter()
        ttft: float | None = None
        text_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}{_OPENAI_CHAT_PATH}", headers=self._headers(), json=payload
                ) as response:
                    if response.status_code >= 400:
                        raise InferenceError(f"The server returned HTTP {response.status_code}.")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            raise InferenceError("The server returned a malformed streaming event.")
                        choices = event.get("choices") or []
                        if event.get("usage"):
                            usage = event["usage"]
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            if ttft is None:
                                ttft = (time.perf_counter() - started) * 1000
                            text_parts.append(content)
                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])
                        for position, raw_call in enumerate(delta.get("tool_calls") or []):
                            if not isinstance(raw_call, dict):
                                continue
                            index = raw_call.get("index", position)
                            if not isinstance(index, int):
                                index = position
                            buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if isinstance(raw_call.get("id"), str):
                                buffer["id"] = raw_call["id"]
                            function = raw_call.get("function") or {}
                            if isinstance(function.get("name"), str):
                                buffer["name"] += function["name"]
                            if isinstance(function.get("arguments"), str):
                                buffer["arguments"] += function["arguments"]
        except InferenceError:
            raise
        except httpx.HTTPError as error:
            raise InferenceError("The OpenAI-compatible server is unreachable or timed out.") from error

        tool_calls = []
        for index in sorted(tool_buffers):
            buffer = tool_buffers[index]
            if not buffer["name"]:
                continue
            try:
                arguments: Any = json.loads(buffer["arguments"]) if buffer["arguments"].strip() else {}
            except json.JSONDecodeError:
                arguments = {"_raw": buffer["arguments"]}
            tool_calls.append({"id": buffer["id"] or f"call_{index}", "name": buffer["name"], "arguments": arguments})

        latency = (time.perf_counter() - started) * 1000
        return ChatResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            ttft_ms=ttft,
            latency_ms=latency,
            finish_reason=finish_reason,
        )


class OllamaBackend:
    """Local Ollama chat endpoint."""

    def __init__(self, *, base_url: str, model: str, timeout: float = 180.0, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._api_key = api_key

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        seed: int | None = None,
    ) -> ChatResult:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover
            raise InferenceError("httpx is required for inference: pip install httpx") from error

        options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
        if seed is not None:
            options["seed"] = seed
        payload = {"model": self.model, "messages": _payload_messages(messages), "stream": True, "options": options}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.perf_counter()
        ttft: float | None = None
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        raise InferenceError(f"Ollama returned HTTP {response.status_code}.")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            raise InferenceError("Ollama returned a malformed streaming event.")
                        if event.get("error"):
                            raise InferenceError("Ollama could not complete the request.")
                        message = event.get("message") or {}
                        content = message.get("content")
                        if isinstance(content, str) and content:
                            if ttft is None:
                                ttft = (time.perf_counter() - started) * 1000
                            text_parts.append(content)
                        for raw_call in message.get("tool_calls") or []:
                            if not isinstance(raw_call, dict):
                                continue
                            function = raw_call.get("function") or {}
                            name = function.get("name")
                            if isinstance(name, str) and name:
                                arguments = function.get("arguments", {})
                                if isinstance(arguments, str):
                                    try:
                                        arguments = json.loads(arguments)
                                    except json.JSONDecodeError:
                                        arguments = {"_raw": arguments}
                                tool_calls.append({"id": str(raw_call.get("id") or f"call_{len(tool_calls)}"), "name": name, "arguments": arguments})
                        if event.get("done"):
                            if isinstance(event.get("eval_count"), int):
                                usage["completion_tokens"] = event["eval_count"]
                            if isinstance(event.get("prompt_eval_count"), int):
                                usage["prompt_tokens"] = event["prompt_eval_count"]
                            break
        except InferenceError:
            raise
        except httpx.HTTPError as error:
            raise InferenceError("Ollama is unreachable or timed out.") from error

        latency = (time.perf_counter() - started) * 1000
        return ChatResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            ttft_ms=ttft,
            latency_ms=latency,
        )


class VSutraBackend:
    """The main V-Sutra backend's streaming chat route."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 240.0,
        enabled_tools: list[str] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.enabled_tools = enabled_tools or []
        self._api_key = api_key

    @property
    def name(self) -> str:
        return f"vsutra:{self.model}"

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        seed: int | None = None,
    ) -> ChatResult:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover
            raise InferenceError("httpx is required for inference: pip install httpx") from error

        del seed  # V-Sutra's chat route does not accept a sampling seed.
        payload = {
            "model": self.model,
            "messages": _payload_messages(messages),
            "conversation_id": f"research-{uuid.uuid4().hex}",
            "context_turn_limit": 24,
            "enabled_tools": self.enabled_tools,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.perf_counter()
        ttft: float | None = None
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        notices: list[str] = []
        sources: list[str] = []
        search_sources: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat/stream", headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        raise InferenceError(f"V-Sutra returned HTTP {response.status_code}.")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            raise InferenceError("V-Sutra returned a malformed streaming event.")
                        kind = event.get("type")
                        if kind == "error":
                            raise InferenceError(str(event.get("error", "V-Sutra generation failed.")))
                        if kind == "token" and isinstance(event.get("token"), str):
                            if ttft is None:
                                ttft = (time.perf_counter() - started) * 1000
                            text_parts.append(event["token"])
                        elif kind == "notice" and isinstance(event.get("message"), str):
                            notices.append(event["message"])
                        elif kind == "tool_request" and isinstance(event.get("tool"), str):
                            tool_calls.append({
                                "id": str(event.get("call_id", "")),
                                "name": event["tool"],
                                "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                            })
                        elif kind == "done":
                            sources = [str(item) for item in event.get("sources") or []]
                            search_sources = [item for item in event.get("search_sources") or [] if isinstance(item, dict)]
        except InferenceError:
            raise
        except httpx.HTTPError as error:
            raise InferenceError("The V-Sutra backend is unreachable or timed out.") from error

        latency = (time.perf_counter() - started) * 1000
        return ChatResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage={"sources": sources, "search_sources": search_sources} if sources or search_sources else {},
            ttft_ms=ttft,
            latency_ms=latency,
            notices=notices,
        )


def build_backend(kind: str, config: dict[str, Any]) -> OpenAIBackend | OllamaBackend | VSutraBackend:
    kind = kind.lower()
    base_url = str(config.get("base_url", "")).rstrip("/")
    model = str(config.get("model", ""))
    timeout = float(config.get("timeout_seconds", 180))
    api_key = config.get("api_key")
    api_key_env = config.get("api_key_env")
    if not base_url or not model:
        raise InferenceError("A backend base URL and model name are required.")
    if kind == "openai":
        return OpenAIBackend(base_url=base_url, model=model, api_key=api_key, api_key_env=api_key_env, timeout=timeout)
    if kind == "ollama":
        return OllamaBackend(base_url=base_url, model=model, timeout=timeout, api_key=_api_key(api_key, api_key_env))
    if kind == "vsutra":
        tools = config.get("enabled_tools") if isinstance(config.get("enabled_tools"), list) else []
        return VSutraBackend(base_url=base_url, model=model, timeout=timeout, enabled_tools=[str(item) for item in tools], api_key=_api_key(api_key, api_key_env))
    raise InferenceError("backend kind must be openai, ollama, or vsutra")


def compatibility_manifest(
    *,
    name: str,
    version: str,
    base_url: str,
    model: str,
    context_window: int,
    capabilities: list[str],
    tool_support_verified: bool = False,
    vision_support_verified: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    """Build the manifest that lets V-Sutra trust a new model endpoint truthfully."""
    normalized = [item.strip().lower() for item in capabilities if item.strip()]
    unknown = set(normalized) - ALLOWED_CAPABILITIES
    if unknown:
        raise ValueError(f"Unknown capabilities: {', '.join(sorted(unknown))}")
    if "chat" not in normalized:
        raise ValueError("Every serving endpoint must declare the chat capability.")
    if "tools" in normalized and not tool_support_verified:
        raise ValueError("Declare the tools capability only after verifying tool calls against this endpoint.")
    if "vision" in normalized and not vision_support_verified:
        raise ValueError("Declare the vision capability only after verifying image input against this endpoint.")
    if context_window < 512:
        raise ValueError("context_window must be at least 512 tokens")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must be an http(s) URL")

    return {
        "schema": "vsutra-inference-compat-v1",
        "model": {
            "id": model,
            "name": name,
            "version": version,
            "context_window": context_window,
            "capabilities": normalized,
            "verification": {
                "tools": bool(tool_support_verified),
                "vision": bool(vision_support_verified),
            },
        },
        "serving": {
            "protocol": "openai-chat-completions",
            "base_url": base_url.rstrip("/"),
            "chat_path": _OPENAI_CHAT_PATH,
            "health_path": "/models",
        },
        "v_sutra_env": {
            "CUSTOM_MODEL_NAME": f"{name} {version}",
            "CUSTOM_MODEL_ID": model,
            "CUSTOM_MODEL_BASE_URL": base_url.rstrip("/"),
            "CUSTOM_MODEL_CONTEXT_WINDOW": str(context_window),
            "CUSTOM_MODEL_CAPABILITIES": ",".join(normalized),
        },
        "requirements": [
            "Serve the model with an OpenAI-compatible chat completions API.",
            "Declare only capabilities that were verified against this exact endpoint.",
            "Keep the endpoint private or behind an authenticated gateway; do not expose API keys to the browser.",
            "Record the manifest and evaluation report together in the model registry.",
        ],
        "notes": notes,
    }


def manifest_env_lines(manifest: dict[str, Any]) -> list[str]:
    env = manifest.get("v_sutra_env")
    if not isinstance(env, dict):
        raise ValueError("Manifest does not contain v_sutra_env.")
    return [f"{key}={value}" for key, value in env.items()]


def ollama_modelfile(*, gguf_path: str, system: str = "", template: str = "", parameters: dict[str, Any] | None = None) -> str:
    """Render an Ollama Modelfile for a quantized GGUF export, without running anything."""
    if not gguf_path.strip():
        raise ValueError("A GGUF path is required.")
    lines = [f"FROM {gguf_path.strip()}"]
    if system.strip():
        lines.append(f'SYSTEM """{system.strip()}"""')
    if template.strip():
        lines.append(f'TEMPLATE """{template.strip()}"""')
    for key, value in (parameters or {}).items():
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"PARAMETER {key} {value}")
    return "\n".join(lines) + "\n"


async def collect(backend: OpenAIBackend | OllamaBackend | VSutraBackend, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
    return await backend.generate(messages, **kwargs)


def run(coroutine: Any) -> Any:
    """Small convenience wrapper for synchronous callers and tests."""
    return asyncio.run(coroutine)
