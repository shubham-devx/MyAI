from app.config import settings
from app.providers.openai_compatible import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    provider_id = "mistral"
    display_name = "Mistral"
    # Mistral documents this path as its OpenAI-compatible Chat Completions API.
    completion_path = "/openai/v1/chat/completions"

    def __init__(self) -> None:
        super().__init__(
            api_key=settings.mistral_api_key,
            base_url=settings.mistral_base_url,
            model_names=settings.parse_csv(settings.mistral_models),
            context_windows=settings.parse_int_map(settings.mistral_model_context_windows),
        )
