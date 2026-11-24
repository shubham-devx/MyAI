"""Model-driven tool loop shared by the streaming chat route."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.config import settings
from app.confirmations import confirmation_broker
from app.providers.base import ModelProvider, ProviderError, ToolCall
from app.schemas import ChatMessage, ModelInfo, ToolCallPayload
from app.tools.base import ToolContext, ToolError, ToolResult
from app.tools.registry import tools
from app.tools.web_search import provider_status

logger = logging.getLogger(__name__)


def _result_payload(call: ToolCall, result: ToolResult) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "call_id": call.id,
        "tool": call.name,
        "status": result.status,
        "summary": result.summary,
        "data": result.data,
        "sources": [source.as_dict() for source in result.sources],
    }


async def _execute_and_capture(
    call: ToolCall,
    arguments: dict[str, Any],
    conversation_id: str | None,
    document_ids: list[str],
) -> ToolResult:
    context = ToolContext(conversation_id=conversation_id, document_ids=document_ids)
    try:
        return await tools.execute(call.name, arguments, context)
    except ToolError as error:
        return ToolResult(content=f"Tool error: {error.message}", summary=error.message, status="error")
    except Exception:
        logger.exception("Tool execution failed: %s", call.name)
        return ToolResult(
            content="The tool failed unexpectedly. Continue without its output and explain the limitation.",
            summary="Tool execution failed",
            status="error",
        )


async def stream_agent_reply(
    provider: ModelProvider,
    provider_model: str,
    model: ModelInfo,
    messages: list[ChatMessage],
    enabled_tools: list[str],
    conversation_id: str | None,
    document_ids: list[str],
    document_sources: list[str],
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[dict[str, Any]]:
    requested = [name for name in enabled_tools if name in tools.specs]
    use_tools = bool(requested) and provider.supports_tools
    notices: list[str] = []
    if requested and not provider.supports_tools:
        notices.append(f"{provider.display_name} does not support native tool calling for this model; continuing without tools.")
    if provider.provider_id == "ollama" and requested and "tools" not in model.capabilities:
        notices.append("The selected Ollama model does not report tool support; continuing without tools.")
        use_tools = False
    if "web_search" in requested and not provider_status()["configured"]:
        notices.append("Web search is not configured on the backend. Configure a search provider to enable it.")
        requested = [name for name in requested if name != "web_search"]
    if "analyze_csv" in requested and not document_ids:
        requested = [name for name in requested if name != "analyze_csv"]
    for notice in notices:
        yield {"type": "notice", "message": notice}

    has_csv = bool(document_ids)
    tool_schemas = tools.schemas(requested, has_csv=has_csv) if use_tools else []
    working = list(messages)
    search_sources: list[dict[str, Any]] = []
    tools_used: list[dict[str, str]] = []
    rounds = 0
    final_attempts = 0
    tool_fallback_used = False

    while True:
        allow_tools = bool(tool_schemas) and rounds < settings.tools_max_rounds
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        try:
            async for event in provider.stream_chat_events(working, provider_model, tool_schemas if allow_tools else None):
                if await is_disconnected():
                    return
                if event.text:
                    text_parts.append(event.text)
                    yield {"type": "token", "token": event.text}
                if event.tool_calls:
                    calls.extend(event.tool_calls)
        except ProviderError as error:
            if allow_tools and not text_parts and not calls and not tool_fallback_used:
                tool_fallback_used = True
                tool_schemas = []
                yield {
                    "type": "notice",
                    "message": (
                        f"{provider.display_name} rejected native tool calling for this model "
                        f"({error.message}). Continuing without tools for this response."
                    ),
                }
                continue
            raise

        if not calls:
            yield {
                "type": "done",
                "model": model.id,
                "provider": provider.provider_id,
                "sources": sorted(set(document_sources)),
                "search_sources": search_sources,
                "tools_used": tools_used,
            }
            return

        if not allow_tools:
            final_attempts += 1
            if final_attempts > 1:
                yield {
                    "type": "done",
                    "model": model.id,
                    "provider": provider.provider_id,
                    "sources": sorted(set(document_sources)),
                    "search_sources": search_sources,
                    "tools_used": tools_used,
                }
                return
            working.append(ChatMessage(
                role="system",
                content="Tool use is no longer available. Answer the user's question with the information already gathered.",
            ))
            tool_schemas = []
            continue

        assistant_text = "".join(text_parts)
        working.append(ChatMessage(
            role="assistant",
            content=assistant_text,
            tool_calls=[ToolCallPayload(id=call.id, name=call.name, arguments=call.arguments) for call in calls],
        ))

        for call in calls:
            if await is_disconnected():
                return
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            yield {
                "type": "tool_request",
                "call_id": call.id,
                "tool": call.name,
                "arguments": arguments,
            }

            if not tools.is_enabled(call.name, requested, has_csv=has_csv):
                result = ToolResult(
                    content=f"Tool '{call.name}' is not enabled for this request.",
                    summary="Tool not enabled",
                    status="error",
                )
            else:
                spec = tools.specs[call.name]
                if spec.requires_confirmation:
                    confirmation_id, future = confirmation_broker.begin(call.name, arguments)
                    yield {
                        "type": "tool_confirmation",
                        "confirmation_id": confirmation_id,
                        "call_id": call.id,
                        "tool": call.name,
                        "arguments": arguments,
                        "expires_in": settings.tool_confirmation_timeout_seconds,
                    }
                    approved = await confirmation_broker.wait(
                        confirmation_id,
                        future,
                        settings.tool_confirmation_timeout_seconds,
                        is_disconnected,
                    )
                    if await is_disconnected():
                        return
                    if approved is True:
                        result = await _execute_and_capture(call, arguments, conversation_id, document_ids)
                    else:
                        reason = "timed out" if approved is None else "was denied"
                        result = ToolResult(
                            content=(
                                f"The user {reason} permission to run this code. Do not claim the code ran. "
                                "Explain what it would do or ask for an alternative."
                            ),
                            summary=f"Execution {reason} by user",
                            status="denied",
                        )
                else:
                    result = await _execute_and_capture(call, arguments, conversation_id, document_ids)

            if len(result.content) > settings.tool_max_result_chars:
                result.content = result.content[: settings.tool_max_result_chars] + "\n[Tool output truncated to the configured limit.]"
            working.append(ChatMessage(
                role="tool",
                tool_call_id=call.id,
                name=call.name,
                content=result.content,
            ))
            tools_used.append({"name": call.name, "status": result.status, "summary": result.summary})
            for source in result.sources:
                entry = source.as_dict()
                if entry not in search_sources:
                    search_sources.append(entry)
            yield _result_payload(call, result)

        rounds += 1
