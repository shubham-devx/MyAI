import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.providers.base import ModelProvider, ProviderError, StreamEvent, ToolCall
from app.schemas import ChatMessage, ModelInfo


class OpenAICompatibleProvider(ModelProvider):
    """Adapter for providers that expose the OpenAI chat-completions wire format."""

    api_key: str | None
    base_url: str
    timeout: float = 120.0
    model_names: list[str]
    completion_path = "/chat/completions"
    supports_tools = True

    def __init__(self, *, api_key: str | None, base_url: str, model_names: list[str], allow_no_auth: bool = False, context_windows: dict[str, int] | None = None) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.base_url = base_url.rstrip("/")
        self.model_names = model_names
        self.context_windows = context_windows or {}
        self.allow_no_auth = allow_no_auth
        self.configured = bool(self.api_key)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=f"{self.provider_id}:{name}",
                provider=self.provider_id,
                name=name,
                context_window=self.context_windows.get(name),
                capabilities=["chat", "streaming"],
                available=self.configured,
                configuration_required=not self.configured,
            )
            for name in self.model_names
        ]

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            if self.allow_no_auth:
                return {"Content-Type": "application/json"}
            raise ProviderError(f"Configure the {self.display_name} API key on the backend first.", 503)
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in messages:
            if item.role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": item.tool_call_id or "",
                    "content": item.content,
                })
            elif item.role == "assistant" and item.tool_calls:
                result.append({
                    "role": "assistant",
                    "content": item.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments if isinstance(call.arguments, (dict, list)) else {}, ensure_ascii=False),
                            },
                        }
                        for call in item.tool_calls
                    ],
                })
            else:
                result.append({"role": item.role, "content": item.content})
        return result

    @staticmethod
    def _text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ""

    @staticmethod
    def _error(error: httpx.HTTPError, provider_name: str) -> ProviderError:
        if isinstance(error, httpx.ConnectError):
            return ProviderError(f"{provider_name} API is unreachable.", 503)
        if isinstance(error, httpx.HTTPStatusError):
            code = error.response.status_code
            if code in (401, 403):
                return ProviderError(f"{provider_name} rejected the configured credentials or access to this model.", 502)
            if code == 404:
                return ProviderError(f"The selected {provider_name} model is unavailable.", 404)
            if code == 429:
                return ProviderError(f"{provider_name} rate limit was reached. Try again shortly.", 429)
        return ProviderError(f"{provider_name} could not complete the request.", 502)

    def _payload(
        self,
        messages: list[ChatMessage],
        model: str,
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._messages(messages),
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        return payload

    async def complete(self, messages: list[ChatMessage], model: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}{self.completion_path}",
                    headers=self._headers(),
                    json=self._payload(messages, model, False),
                )
                response.raise_for_status()
                content = self._text(response.json()["choices"][0]["message"]["content"])
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            if isinstance(error, httpx.HTTPError):
                raise self._error(error, self.display_name) from error
            raise ProviderError(f"{self.display_name} returned an invalid completion response.") from error
        if not isinstance(content, str):
            raise ProviderError(f"{self.display_name} returned a completion without text content.")
        return content

    async def complete_structured(
        self,
        messages: list[ChatMessage],
        model: str,
        schema: dict[str, Any],
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}{self.completion_path}",
                    headers=self._headers(),
                    json=self._payload(messages, model, False, response_format={"type": "json_object"}),
                )
                response.raise_for_status()
                content = self._text(response.json()["choices"][0]["message"]["content"])
                if isinstance(content, str) and content.strip():
                    return content
                raise ProviderError(f"{self.display_name} returned an empty structured response.")
        except ProviderError:
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {400, 404, 422}:
                # Some OpenAI-compatible servers do not implement response_format.
                return await super().complete_structured(messages, model, schema)
            raise self._error(error, self.display_name) from error
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            if isinstance(error, httpx.HTTPError):
                raise self._error(error, self.display_name) from error
            raise ProviderError(f"{self.display_name} returned an invalid structured response.") from error

    async def stream_chat_events(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        tool_buffers: dict[int, dict[str, str]] = {}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}{self.completion_path}",
                    headers=self._headers(),
                    json=self._payload(messages, model, True, tools=tools),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                            choices = event.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {}) or {}
                            content = self._text(delta.get("content"))
                        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
                            raise ProviderError(f"{self.display_name} returned an invalid streaming event.") from error
                        if content:
                            yield StreamEvent(text=content)
                        raw_calls = delta.get("tool_calls")
                        if isinstance(raw_calls, list):
                            for position, raw_call in enumerate(raw_calls):
                                if not isinstance(raw_call, dict):
                                    continue
                                index = raw_call.get("index", position)
                                if not isinstance(index, int):
                                    index = position
                                buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                if isinstance(raw_call.get("id"), str) and raw_call["id"]:
                                    buffer["id"] = raw_call["id"]
                                function = raw_call.get("function") or {}
                                if isinstance(function.get("name"), str) and function["name"]:
                                    buffer["name"] = function["name"]
                                arguments = function.get("arguments")
                                if isinstance(arguments, str):
                                    buffer["arguments"] += arguments
                                elif isinstance(arguments, dict):
                                    buffer["arguments"] += json.dumps(arguments, ensure_ascii=False)
        except ProviderError:
            raise
        except httpx.HTTPError as error:
            raise self._error(error, self.display_name) from error

        calls: list[ToolCall] = []
        for index in sorted(tool_buffers):
            buffer = tool_buffers[index]
            if not buffer["name"]:
                continue
            try:
                arguments: Any = json.loads(buffer["arguments"]) if buffer["arguments"].strip() else {}
            except json.JSONDecodeError:
                arguments = buffer["arguments"]
            calls.append(ToolCall(
                id=buffer["id"] or f"call_{uuid.uuid4().hex[:16]}",
                name=buffer["name"],
                arguments=arguments,
            ))
        if calls:
            yield StreamEvent(tool_calls=calls)

    async def stream_chat(self, messages: list[ChatMessage], model: str) -> AsyncIterator[str]:
        async for event in self.stream_chat_events(messages, model):
            if event.text:
                yield event.text
