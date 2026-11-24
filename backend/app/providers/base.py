from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.schemas import ChatMessage, ModelInfo


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass
class StreamEvent:
    text: str = ""
    tool_calls: list[ToolCall] | None = None


class ModelProvider(ABC):
    provider_id: str
    display_name: str
    configured: bool
    configuration_hint: str | None = None
    supports_tools: bool = False

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def complete(self, messages: list[ChatMessage], model: str) -> str: ...

    @abstractmethod
    async def stream_chat(self, messages: list[ChatMessage], model: str) -> AsyncIterator[str]: ...

    async def stream_chat_events(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Provider-neutral event stream. Non-tool providers fall back to text only."""
        async for token in self.stream_chat(messages, model):
            yield StreamEvent(text=token)

    async def complete_structured(
        self,
        messages: list[ChatMessage],
        model: str,
        schema: dict[str, Any],
    ) -> str:
        """Return raw JSON text. Providers with native JSON modes override this."""
        return await self.complete(messages, model)
