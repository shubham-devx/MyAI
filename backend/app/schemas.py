from typing import Any, Literal
import base64
import binascii
import io
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from PIL import Image


class ToolCallPayload(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    arguments: Any = Field(default_factory=dict)


class ChatMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(default="", max_length=32_000)
    images: list[str] = Field(default_factory=list, max_length=4)
    tool_calls: list[ToolCallPayload] = Field(default_factory=list, max_length=8)
    tool_call_id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=64)

    @field_validator("images")
    @classmethod
    def validate_images(cls, images: list[str]) -> list[str]:
        for image in images:
            if len(image) > 12 * 1024 * 1024:
                raise ValueError("Each image must be 8 MB or smaller.")
            try:
                decoded = base64.b64decode(image, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Images must be valid base64 encoded image bytes.") from error
            if len(decoded) > 8 * 1024 * 1024:
                raise ValueError("Each image must be 8 MB or smaller.")
            try:
                with Image.open(io.BytesIO(decoded)) as opened:
                    if opened.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError("Only JPEG, PNG, and WebP images are supported.")
                    opened.verify()
            except ValueError:
                raise
            except Exception as error:
                raise ValueError("An uploaded image is corrupted or has an unsupported format.") from error
        return images

    @model_validator(mode="after")
    def validates_message_shape(self) -> "ChatMessage":
        if self.role in {"system", "user"} and not self.content.strip():
            raise ValueError("System and user messages must contain text.")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("Tool result messages must reference a tool call.")
        if self.role == "assistant" and not self.content.strip() and not self.tool_calls:
            raise ValueError("Assistant messages must contain text or tool calls.")
        return self


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    context_turn_limit: int = Field(default=8, ge=1, le=24)
    enabled_tools: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("enabled_tools")
    @classmethod
    def validates_tool_names(cls, names: list[str]) -> list[str]:
        for name in names:
            if not name or len(name) > 64:
                raise ValueError("Invalid tool name.")
        return list(dict.fromkeys(names))

    @model_validator(mode="after")
    def requires_a_user_message(self) -> "ChatCompletionRequest":
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("At least one user message is required.")
        return self


class ToolConfirmationRequest(BaseModel):
    approved: bool


class CalculatorRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expression: str = Field(min_length=1, max_length=500)


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=400)
    max_results: int = Field(default=5, ge=1, le=10)


class CodeGenerationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=12_000)
    language: str = Field(default="python", min_length=1, max_length=40)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    requirements: str = Field(default="", max_length=4_000)


class SandboxRunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    language: Literal["python", "javascript"]
    code: str = Field(min_length=1, max_length=20_000)
    stdin: str = Field(default="", max_length=10_000)
    confirmed: bool = False


class StructuredOutputRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=12_000)
    schema_: dict[str, Any] = Field(alias="schema", default_factory=dict)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    system: str = Field(default="", max_length=4_000)


class SpeechRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4_000)


class SignupRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=128)


class ModelInfo(BaseModel):
    id: str
    provider: str
    name: str
    context_window: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    available: bool = True
    configuration_required: bool = False
    size: int | None = None
    modified_at: str | None = None


class ProviderInfo(BaseModel):
    id: str
    name: str
    configured: bool
    available: bool
    configuration_hint: str | None = None


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
    providers: list[ProviderInfo]
    default_model: str | None = None
