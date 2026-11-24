import json
import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.providers.base import ModelProvider, ProviderError, StreamEvent, ToolCall
from app.schemas import ChatMessage, ModelInfo


class OllamaProvider(ModelProvider):
    provider_id = "ollama"
    display_name = "Ollama"
    configured = True
    configuration_hint = None
    supports_tools = True

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.timeout = settings.ollama_request_timeout_seconds

    @staticmethod
    def _messages(messages: list[ChatMessage]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for message in messages:
            if message.role == "tool":
                result.append({
                    "role": "tool",
                    "content": message.content,
                    "tool_name": message.name or "",
                })
                continue
            item: dict[str, object] = {"role": message.role, "content": message.content}
            if message.images:
                item["images"] = message.images
            if message.role == "assistant" and message.tool_calls:
                item["tool_calls"] = [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments if isinstance(call.arguments, dict) else {},
                        }
                    }
                    for call in message.tool_calls
                ]
            result.append(item)
        return result

    @staticmethod
    def _provider_error(error: httpx.HTTPError) -> ProviderError:
        if isinstance(error, httpx.ConnectError):
            return ProviderError("Ollama is unavailable. Start the local Ollama service and try again.", 503)
        if isinstance(error, httpx.TimeoutException):
            return ProviderError("Ollama did not respond before the configured request timeout.", 504)
        if isinstance(error, httpx.HTTPStatusError):
            if error.response.status_code == 404:
                return ProviderError("The requested model is not installed locally.", 404)
            return ProviderError("Ollama could not complete the request.", 502)
        return ProviderError("The local model service returned an invalid response.", 502)

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise self._provider_error(error) from error

        try:
            models = response.json().get("models", [])
            return list(await asyncio.gather(*(self._model_info(model) for model in models if model.get("name"))))
        except (ValueError, AttributeError, TypeError, KeyError) as error:
            raise ProviderError("Ollama returned an invalid model list.") from error

    async def _model_info(self, model: dict[str, object]) -> ModelInfo:
        name = str(model["name"])
        context_window: int | None = None
        capabilities = ["chat", "streaming"]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/show", json={"name": name})
                response.raise_for_status()
            details = response.json()
            info = details.get("model_info", {})
            context_window = next(
                (value for key, value in info.items() if key.endswith(".context_length") and isinstance(value, int)),
                None,
            )
            extra_capabilities = details.get("capabilities", [])
            if isinstance(extra_capabilities, list):
                capabilities.extend(str(value) for value in extra_capabilities if value not in capabilities)
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return ModelInfo(
            id=f"ollama:{name}",
            provider=self.provider_id,
            name=name,
            context_window=context_window,
            capabilities=capabilities,
            available=True,
            size=model.get("size") if isinstance(model.get("size"), int) else None,
            modified_at=model.get("modified_at") if isinstance(model.get("modified_at"), str) else None,
        )

    async def complete(self, messages: list[ChatMessage], model: str) -> str:
        payload = {"model": model, "messages": self._messages(messages), "stream": False}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise self._provider_error(error) from error

        try:
            content = response.json().get("message", {}).get("content")
        except (ValueError, AttributeError) as error:
            raise ProviderError("Ollama returned an invalid completion response.") from error
        if not isinstance(content, str):
            raise ProviderError("Ollama returned a completion without message content.")
        return content

    async def complete_structured(
        self,
        messages: list[ChatMessage],
        model: str,
        schema: dict[str, Any],
    ) -> str:
        base_payload: dict[str, Any] = {
            "model": model,
            "messages": self._messages(messages),
            "stream": False,
        }
        for format_value in (schema, "json"):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(f"{self.base_url}/api/chat", json={**base_payload, "format": format_value})
                    response.raise_for_status()
                content = response.json().get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    return content
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {400, 422}:
                    raise self._provider_error(error) from error
            except httpx.HTTPError as error:
                raise self._provider_error(error) from error
            except (ValueError, AttributeError, TypeError) as error:
                raise ProviderError("Ollama returned an invalid structured response.") from error
        return await super().complete_structured(messages, model, schema)

    async def stream_chat_events(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {"model": model, "messages": self._messages(messages), "stream": True}
        if tools:
            payload["tools"] = tools
        calls: list[ToolCall] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout))) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("error"):
                            message = str(event["error"])
                            lowered = message.lower()
                            if "tool" in lowered and ("support" in lowered or "not" in lowered):
                                raise ProviderError("The selected Ollama model does not support tool calling.", 400)
                            status = 404 if "not found" in lowered else 502
                            raise ProviderError(
                                "The requested model is not installed locally." if status == 404 else "Ollama could not complete the request.",
                                status,
                            )
                        message = event.get("message", {}) or {}
                        content = message.get("content")
                        if isinstance(content, str) and content:
                            yield StreamEvent(text=content)
                        raw_calls = message.get("tool_calls")
                        if isinstance(raw_calls, list):
                            for raw_call in raw_calls:
                                if not isinstance(raw_call, dict):
                                    continue
                                function = raw_call.get("function") or {}
                                name = function.get("name")
                                if not isinstance(name, str) or not name:
                                    continue
                                arguments: Any = function.get("arguments", {})
                                if isinstance(arguments, str):
                                    try:
                                        arguments = json.loads(arguments)
                                    except json.JSONDecodeError:
                                        pass
                                calls.append(ToolCall(
                                    id=str(raw_call.get("id") or f"call_{uuid.uuid4().hex[:16]}"),
                                    name=name,
                                    arguments=arguments,
                                ))
                        if event.get("done"):
                            break
        except ProviderError:
            raise
        except httpx.HTTPError as error:
            raise self._provider_error(error) from error
        except json.JSONDecodeError as error:
            raise ProviderError("Ollama returned an invalid streaming response.") from error
        if calls:
            yield StreamEvent(tool_calls=calls)

    async def stream_chat(self, messages: list[ChatMessage], model: str) -> AsyncIterator[str]:
        async for event in self.stream_chat_events(messages, model):
            if event.text:
                yield event.text
