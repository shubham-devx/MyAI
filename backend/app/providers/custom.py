from app.config import settings
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.schemas import ModelInfo


class CustomProvider(OpenAICompatibleProvider):
    provider_id = "custom"
    display_name = "Custom infrastructure"

    def __init__(self) -> None:
        self.model_id = settings.custom_model_id
        self.model_name = settings.custom_model_name or settings.custom_model_id
        self.context_window = settings.custom_model_context_window
        self.capabilities = settings.parse_csv(settings.custom_model_capabilities)
        super().__init__(
            api_key=settings.custom_model_api_key,
            base_url=settings.custom_model_base_url or "http://127.0.0.1:8080/v1",
            model_names=[self.model_id] if settings.custom_model_base_url else [],
            allow_no_auth=True,
        )
        self.configured = bool(settings.custom_model_base_url)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=f"{self.provider_id}:{self.model_id}",
                provider=self.provider_id,
                name=self.model_name,
                context_window=self.context_window,
                capabilities=self.capabilities,
                available=self.configured,
                configuration_required=not self.configured,
            )
        ] if self.model_id else []
