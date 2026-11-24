import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # Keep Ollama usable when optional cloud dependencies aren't installed.
    boto3 = None

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        pass

    class NoCredentialsError(Exception):
        pass

from app.config import settings
from app.providers.base import ModelProvider, ProviderError
from app.schemas import ChatMessage, ModelInfo


class BedrockProvider(ModelProvider):
    """Amazon Bedrock Converse adapter; credentials use the standard AWS SDK chain."""

    provider_id = "bedrock"
    display_name = "Amazon Bedrock"

    def __init__(self) -> None:
        self.region = settings.bedrock_region
        self.model_ids = settings.parse_csv(settings.bedrock_models)
        self.context_windows = settings.parse_int_map(settings.bedrock_model_context_windows)
        self.credentials_configured = self._credentials_configured()
        self.configured = bool(self.region and self.model_ids and self.credentials_configured)
        self.configuration_hint = "Set BEDROCK_REGION and BEDROCK_MODELS; configure AWS credentials using the AWS SDK chain."
        self._client: Any = None

    @staticmethod
    def _credentials_configured() -> bool:
        credential_env = (
            "AWS_ACCESS_KEY_ID",
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        )
        if any(os.getenv(key) for key in credential_env):
            return True
        credentials = Path(os.getenv("AWS_SHARED_CREDENTIALS_FILE", str(Path.home() / ".aws" / "credentials")))
        config = Path(os.getenv("AWS_CONFIG_FILE", str(Path.home() / ".aws" / "config")))
        return credentials.is_file() or config.is_file()

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=f"bedrock:{model_id}",
                provider=self.provider_id,
                name=model_id,
                context_window=self.context_windows.get(model_id),
                capabilities=["chat", "streaming"] if self.configured else ["chat"],
                available=self.configured,
                configuration_required=not self.configured,
            )
            for model_id in self.model_ids
        ]

    def _get_client(self) -> Any:
        if not self.configured:
            raise ProviderError("Configure a Bedrock region and model on the backend first.", 503)
        if boto3 is None:
            raise ProviderError("Install the backend optional dependency boto3 to use Bedrock.", 503)
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    @staticmethod
    def _messages(messages: list[ChatMessage]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        system = [{"text": message.content} for message in messages if message.role == "system"]
        conversation = [
            {"role": message.role, "content": [{"text": message.content}]}
            for message in messages
            if message.role in ("user", "assistant")
        ]
        return conversation, system

    async def complete(self, messages: list[ChatMessage], model: str) -> str:
        conversation, system = self._messages(messages)
        try:
            client = await asyncio.to_thread(self._get_client)
            response = await asyncio.to_thread(
                client.converse,
                modelId=model,
                messages=conversation,
                system=system,
            )
            return "".join(
                part.get("text", "")
                for part in response.get("output", {}).get("message", {}).get("content", [])
                if isinstance(part.get("text"), str)
            )
        except (BotoCoreError, ClientError, NoCredentialsError) as error:
            raise self._provider_error(error) from error

    async def stream_chat(self, messages: list[ChatMessage], model: str) -> AsyncIterator[str]:
        conversation, system = self._messages(messages)
        stream: Any = None
        try:
            client = await asyncio.to_thread(self._get_client)
            response = await asyncio.to_thread(
                client.converse_stream,
                modelId=model,
                messages=conversation,
                system=system,
            )
            stream = response["stream"]
            iterator = iter(stream)
            while True:
                event = await asyncio.to_thread(self._next_event, iterator)
                if event is None:
                    break
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if isinstance(delta, str) and delta:
                    yield delta
        except asyncio.CancelledError:
            if stream is not None and hasattr(stream, "close"):
                await asyncio.to_thread(stream.close)
            raise
        except (BotoCoreError, ClientError, NoCredentialsError, KeyError) as error:
            raise self._provider_error(error) from error

    @staticmethod
    def _next_event(iterator: Any) -> dict[str, Any] | None:
        try:
            return next(iterator)
        except StopIteration:
            return None

    @staticmethod
    def _provider_error(error: Exception) -> ProviderError:
        if isinstance(error, NoCredentialsError):
            return ProviderError("AWS credentials are not configured for Bedrock.", 503)
        if isinstance(error, ClientError):
            code = error.response.get("Error", {}).get("Code", "")
            if code in {"AccessDeniedException", "UnrecognizedClientException", "InvalidSignatureException"}:
                return ProviderError("AWS denied access. Check the Bedrock credentials and model permissions.", 502)
            if code in {"ResourceNotFoundException", "ValidationException"}:
                return ProviderError("The Bedrock model is unavailable in this region or does not support this request.", 404)
        return ProviderError("Amazon Bedrock could not complete the request.", 502)
