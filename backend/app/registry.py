from app.config import settings
from app.providers.bedrock import BedrockProvider
from app.providers.base import ModelProvider, ProviderError
from app.providers.custom import CustomProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.mistral import MistralProvider
from app.providers.ollama import OllamaProvider
from app.schemas import ModelInfo, ModelListResponse, ProviderInfo


class ProviderRegistry:
    """Owns provider adapters and resolves normalized provider:model identifiers."""

    def __init__(self, providers: list[ModelProvider] | None = None) -> None:
        configured_providers = providers or [
            OllamaProvider(),
            DeepSeekProvider(),
            BedrockProvider(),
            MistralProvider(),
            CustomProvider(),
        ]
        self.providers = {provider.provider_id: provider for provider in configured_providers}

    async def catalog(self) -> ModelListResponse:
        models: list[ModelInfo] = []
        provider_status: list[ProviderInfo] = []
        for provider in self.providers.values():
            available = provider.configured
            try:
                provider_models = await provider.list_models()
                models.extend(provider_models)
                if provider.provider_id == "ollama":
                    available = True
            except ProviderError:
                available = False
            provider_status.append(
                ProviderInfo(
                    id=provider.provider_id,
                    name=provider.display_name,
                    configured=provider.configured,
                    available=available,
                    configuration_hint=provider.configuration_hint,
                )
            )

        default_model = self._resolve_default(models)
        return ModelListResponse(models=models, providers=provider_status, default_model=default_model)

    @staticmethod
    def _resolve_default(models: list[ModelInfo]) -> str | None:
        preferred = settings.ollama_model
        for model in models:
            if model.provider == "ollama" and model.available and (
                model.name == preferred or model.name.startswith(f"{preferred}:")
            ):
                return model.id
        # Never preselect a paid or remote provider, even when one is configured.
        return None

    async def resolve(self, model_id: str | None) -> tuple[ModelProvider, str, ModelInfo]:
        catalog = await self.catalog()
        selected_id = model_id or catalog.default_model
        if not selected_id or ":" not in selected_id:
            raise ProviderError("Select an available model before sending a message.", 400)
        provider_id, model_name = selected_id.split(":", 1)
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ProviderError("The selected model provider is not registered.", 404)
        model = next((item for item in catalog.models if item.id == selected_id), None)
        if model is None:
            raise ProviderError("The selected model is not configured on the backend.", 404)
        if not model.available:
            message = f"Configure the {provider.display_name} provider on the backend first." if model.configuration_required else f"The {provider.display_name} model is currently unavailable."
            raise ProviderError(message, 503)
        return provider, model_name, model


registry = ProviderRegistry()
