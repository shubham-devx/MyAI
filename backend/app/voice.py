"""Configurable speech-to-text and text-to-speech integrations.

Local and external engines both use the OpenAI-compatible audio routes, so a local
whisper.cpp server, a self-hosted TTS server, or a hosted provider can be selected with
backend environment variables. The browser's built-in engines remain a no-setup option.
"""

from pathlib import PurePath
from typing import Any

import httpx

from app.config import settings
from app.providers.base import ProviderError

_ALLOWED_AUDIO_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".oga", ".flac", ".mp4"}
_ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/flac",
    "audio/x-m4a",
    "audio/m4a",
    "application/octet-stream",
}
_MEDIA_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac", "pcm": "audio/L16"}


def _base_url(value: str | None, *, default: str | None = None) -> str | None:
    raw = (value or default or "").strip()
    if not raw:
        return None
    base = raw.rstrip("/")
    if not base:
        return None
    after_scheme = base.split("://", 1)[-1]
    if "/" not in after_scheme:
        base = f"{base}/v1"
    return base


def _stt_config() -> dict[str, Any]:
    provider = (settings.voice_stt_provider or "none").strip().lower()
    if provider == "openai":
        base = _base_url(settings.voice_stt_base_url, default="https://api.openai.com/v1")
        configured = bool(base and settings.voice_stt_api_key and settings.voice_stt_api_key.strip())
        hint = None if configured else "Set VOICE_STT_API_KEY, or use whisper_compatible for a local server."
        label = "OpenAI transcription"
    elif provider == "whisper_compatible":
        base = _base_url(settings.voice_stt_base_url)
        configured = bool(base)
        hint = None if configured else "Set VOICE_STT_BASE_URL to a whisper.cpp or OpenAI-compatible local server (for example http://127.0.0.1:8080/v1)."
        label = "Whisper-compatible server"
    else:
        base = None
        configured = False
        label = "Browser speech recognition"
        hint = "Set VOICE_STT_PROVIDER=whisper_compatible or openai to use a backend transcription engine; otherwise the browser engine is used."
    return {
        "provider": provider,
        "label": label,
        "configured": configured,
        "available": configured,
        "base_url": base,
        "model": settings.voice_stt_model if configured else None,
        "hint": hint,
    }


def _tts_config() -> dict[str, Any]:
    provider = (settings.voice_tts_provider or "browser").strip().lower()
    if provider == "openai":
        base = _base_url(settings.voice_tts_base_url, default="https://api.openai.com/v1")
        configured = bool(base and settings.voice_tts_api_key and settings.voice_tts_api_key.strip())
        hint = None if configured else "Set VOICE_TTS_API_KEY, or use openai_compatible for a local TTS server."
        label = "OpenAI speech"
    elif provider == "openai_compatible":
        base = _base_url(settings.voice_tts_base_url)
        configured = bool(base)
        hint = None if configured else "Set VOICE_TTS_BASE_URL to a local or hosted OpenAI-compatible speech server."
        label = "OpenAI-compatible speech server"
    else:
        base = None
        configured = True
        label = "Browser speech synthesis"
        hint = None
    return {
        "provider": provider if provider in {"browser", "openai", "openai_compatible"} else "browser",
        "label": label,
        "configured": configured,
        "available": configured,
        "base_url": base,
        "model": settings.voice_tts_model if provider != "browser" else None,
        "voice": settings.voice_tts_voice if provider != "browser" else None,
        "format": settings.voice_tts_format,
        "hint": hint,
    }


def voice_status() -> dict[str, Any]:
    return {
        "stt": _stt_config(),
        "tts": _tts_config(),
        "limits": {
            "max_upload_mb": settings.voice_max_upload_mb,
            "max_text_chars": settings.voice_max_text_chars,
        },
    }


def _stt_headers() -> dict[str, str]:
    key = (settings.voice_stt_api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _tts_headers() -> dict[str, str]:
    key = (settings.voice_tts_api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def transcribe(audio: bytes, filename: str, content_type: str, language: str | None = None) -> dict[str, Any]:
    config = _stt_config()
    if not config["available"] or not config["base_url"]:
        raise ProviderError("Backend speech recognition is not configured. Use the browser engine or configure a local/external engine.", 503)
    if not audio:
        raise ProviderError("The recording is empty.", 422)
    if len(audio) > int(settings.voice_max_upload_mb * 1024 * 1024):
        raise ProviderError(f"Audio uploads are limited to {settings.voice_max_upload_mb:g} MB.", 413)
    safe_name = PurePath((filename or "recording.webm").replace("\\", "/")).name
    extension = PurePath(safe_name).suffix.lower()
    if extension not in _ALLOWED_AUDIO_EXTENSIONS:
        raise ProviderError("Unsupported audio format. Use WebM, WAV, MP3, M4A, OGG, or FLAC.", 415)
    if content_type and content_type.split(";", 1)[0].strip().lower() not in _ALLOWED_AUDIO_TYPES:
        raise ProviderError("Unsupported audio content type.", 415)
    data = {"model": settings.voice_stt_model, "response_format": "json"}
    if language:
        data["language"] = language[:12]
    try:
        async with httpx.AsyncClient(timeout=settings.voice_stt_timeout_seconds) as client:
            response = await client.post(
                f"{config['base_url']}/audio/transcriptions",
                headers=_stt_headers(),
                data=data,
                files={"file": (safe_name, audio, content_type or "application/octet-stream")},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            raise ProviderError("The speech engine rejected the configured credentials.", 502) from error
        if error.response.status_code == 404:
            raise ProviderError("The speech engine does not expose /audio/transcriptions at the configured base URL.", 502) from error
        raise ProviderError("The speech engine could not transcribe this recording.", 502) from error
    except httpx.HTTPError as error:
        raise ProviderError("The speech engine is unreachable.", 503) from error
    try:
        body = response.json()
        text = body.get("text") if isinstance(body, dict) else None
    except ValueError:
        text = response.text.strip()
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("The speech engine returned no transcript.", 502)
    return {"text": text.strip(), "provider": config["label"], "model": settings.voice_stt_model}


async def synthesize(text: str) -> tuple[bytes, str]:
    config = _tts_config()
    if config["provider"] == "browser":
        raise ProviderError("Text-to-speech is configured to use the browser engine.", 400)
    if not config["available"] or not config["base_url"]:
        raise ProviderError("The configured text-to-speech engine is not available.", 503)
    content = text.strip()
    if not content:
        raise ProviderError("Enter text to speak.", 422)
    if len(content) > settings.voice_max_text_chars:
        raise ProviderError(f"Speech text is limited to {settings.voice_max_text_chars:,} characters.", 413)
    audio_format = (settings.voice_tts_format or "mp3").lower()
    payload = {
        "model": settings.voice_tts_model,
        "voice": settings.voice_tts_voice,
        "input": content,
        "response_format": audio_format,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.voice_stt_timeout_seconds) as client:
            response = await client.post(
                f"{config['base_url']}/audio/speech",
                headers={"Content-Type": "application/json", **_tts_headers()},
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            raise ProviderError("The speech engine rejected the configured credentials.", 502) from error
        if error.response.status_code == 404:
            raise ProviderError("The speech engine does not expose /audio/speech at the configured base URL.", 502) from error
        raise ProviderError("The speech engine could not generate audio.", 502) from error
    except httpx.HTTPError as error:
        raise ProviderError("The speech engine is unreachable.", 503) from error
    if not response.content:
        raise ProviderError("The speech engine returned empty audio.", 502)
    if len(response.content) > 25 * 1024 * 1024:
        raise ProviderError("The generated audio is too large to play.", 413)
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if not media_type.startswith("audio/"):
        media_type = _MEDIA_TYPES.get(audio_format, "audio/mpeg")
    return response.content, media_type
