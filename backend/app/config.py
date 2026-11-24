from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    trusted_hosts: str = ""
    auth_database_path: str = "./data/auth.sqlite3"
    auth_database_url: str | None = None
    app_web_url: str = "http://localhost:3000"
    brevo_api_key: str | None = None
    brevo_sender_email: str | None = None
    brevo_sender_name: str = "V-Sutra"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    frontend_origins: str = "http://localhost:3000"
    # Per-process limits. For multiple workers these counters are independent.
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_default_requests: int = Field(default=120, ge=1, le=100_000)
    rate_limit_chat_requests: int = Field(default=12, ge=1, le=10_000)
    rate_limit_upload_requests: int = Field(default=10, ge=1, le=10_000)
    max_request_body_mb: int = Field(default=128, ge=1, le=1024)
    max_concurrent_inference_requests: int = Field(default=2, ge=1, le=256)
    ollama_request_timeout_seconds: float = 120.0
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_models: str = "deepseek-flash,deepseek-v4-pro"
    deepseek_model_context_windows: str = ""
    mistral_api_key: str | None = None
    mistral_base_url: str = "https://api.mistral.ai"
    mistral_models: str = "mistral-small-latest,mistral-large-latest"
    mistral_model_context_windows: str = ""
    bedrock_region: str | None = None
    bedrock_models: str = ""
    bedrock_model_context_windows: str = ""
    custom_model_name: str | None = None
    custom_model_id: str = "custom-model"
    custom_model_base_url: str | None = None
    custom_model_api_key: str | None = None
    custom_model_context_window: int | None = None
    custom_model_capabilities: str = "chat,streaming"

    # Tool calling. Tools are opt-in per request; external search is never implicit.
    tools_max_rounds: int = 4
    tool_confirmation_timeout_seconds: float = 120.0
    tool_max_result_chars: int = 12_000

    # Configurable web search provider: brave, tavily, searxng, or none.
    search_provider: str = "none"
    search_max_results: int = 5
    search_timeout_seconds: float = 15.0
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None
    searxng_base_url: str | None = None

    # Isolated code execution. The only supported backends are container runtimes.
    sandbox_enabled: bool = True
    sandbox_engine: str = "auto"
    sandbox_timeout_seconds: float = 10.0
    sandbox_memory_mb: int = 256
    sandbox_cpus: float = 0.5
    sandbox_pids_limit: int = 64
    sandbox_max_output_bytes: int = 20_000
    sandbox_max_code_chars: int = 20_000
    sandbox_max_stdin_chars: int = 10_000
    sandbox_python_image: str = "python:3.12-alpine"
    sandbox_javascript_image: str = "node:22-alpine"

    # Voice input/output. Browser engines are handled by the frontend.
    voice_stt_provider: str = "none"
    voice_stt_base_url: str | None = None
    voice_stt_api_key: str | None = None
    voice_stt_model: str = "whisper-1"
    voice_stt_timeout_seconds: float = 60.0
    voice_tts_provider: str = "browser"
    voice_tts_base_url: str | None = None
    voice_tts_api_key: str | None = None
    voice_tts_model: str = "tts-1"
    voice_tts_voice: str = "alloy"
    voice_tts_format: str = "mp3"
    voice_max_upload_mb: float = 25.0
    voice_max_text_chars: int = 4_000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "production", "test"}:
            raise ValueError("APP_ENV must be development, production, or test.")
        return normalized

    @field_validator("trusted_hosts")
    @classmethod
    def validate_trusted_hosts(cls, value: str) -> str:
        hosts = [host.strip() for host in value.split(",") if host.strip()]
        if "*" in hosts:
            raise ValueError("TRUSTED_HOSTS must contain exact hostnames or IP addresses; wildcard is not allowed.")
        return ",".join(hosts)

    @model_validator(mode="after")
    def require_production_hosts(self):
        if self.app_env == "production" and not self.trusted_hosts:
            raise ValueError("TRUSTED_HOSTS must be set when APP_ENV=production.")
        return self

    @property
    def password_reset_email_enabled(self) -> bool:
        return bool(self.brevo_api_key and self.brevo_sender_email)

    @field_validator("frontend_origins")
    @classmethod
    def validate_frontend_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins or "*" in origins:
            raise ValueError("FRONTEND_ORIGINS must contain exact origins; wildcard CORS is not allowed.")
        if any(
            not origin.startswith(("http://", "https://"))
            or any(char in origin.split("://", 1)[1] for char in "/?#")
            for origin in origins
        ):
            raise ValueError("FRONTEND_ORIGINS entries must be exact http(s) origins without paths.")
        return ",".join(origins)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @staticmethod
    def parse_csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def parse_int_map(value: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for entry in value.split(","):
            name, separator, raw_size = entry.partition("=")
            if separator and name.strip() and raw_size.strip().isdigit():
                result[name.strip()] = int(raw_size.strip())
        return result


settings = Settings()
