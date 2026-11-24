from dataclasses import dataclass, field
from typing import Any


class ToolError(Exception):
    """A safe, user-facing tool failure that can be returned to the model."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    external: bool = False
    requires_confirmation: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolSource:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "provider": self.provider}


@dataclass
class ToolResult:
    content: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[ToolSource] = field(default_factory=list)
    status: str = "ok"


@dataclass
class ToolContext:
    conversation_id: str | None = None
    document_ids: list[str] = field(default_factory=list)
    user_message: str = ""
