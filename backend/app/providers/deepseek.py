from app.config import settings
from app.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    provider_id = "deepseek"
    display_name = "DeepSeek"

    def __init__(self) -> None:
        super().__init__(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model_names=settings.parse_csv(settings.deepseek_models),
            context_windows=settings.parse_int_map(settings.deepseek_model_context_windows),
        )
