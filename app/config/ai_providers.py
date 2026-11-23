"""Create configured story and speech providers."""

from dataclasses import dataclass
from typing import Optional

from app.ai.ollama import JSONGenerator
from app.story.provider import (
    AnthropicStoryProvider,
    GeminiStoryProvider,
    OllamaStoryProvider,
    OpenAIStoryProvider,
    StoryProvider,
    TemplateStoryProvider,
)
from app.voiceover.models import VoiceConfig
from app.voiceover.provider import MockVoiceProvider, OpenAIVoiceProvider, VoiceProvider
from app.voiceover.offline_provider import OfflineTTSProvider
from .settings import Settings


class ProviderConfigurationError(ValueError):
    """Raised when an explicitly selected AI provider is not configured."""


@dataclass(frozen=True)
class AIProviders:
    """Provider instances and TTS settings for one automation run."""

    story: StoryProvider
    voice: VoiceProvider
    voice_config: VoiceConfig


def create_ai_providers(
    settings: Settings, ollama_client: Optional[JSONGenerator] = None
) -> AIProviders:
    """Build providers based on settings, failing early with actionable errors."""
    story_provider = settings.story_provider
    if story_provider in ("mock", "template"):
        story = TemplateStoryProvider()
    elif story_provider == "openai":
        _require(settings.openai_api_key, "OPENAI_API_KEY", story_provider)
        story = OpenAIStoryProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_story_model,
        )
    elif story_provider == "anthropic":
        _require(settings.anthropic_api_key, "ANTHROPIC_API_KEY", story_provider)
        story = AnthropicStoryProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_story_model,
        )
    elif story_provider == "gemini":
        _require(settings.gemini_api_key, "GEMINI_API_KEY", story_provider)
        story = GeminiStoryProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_story_model,
        )
    elif story_provider == "ollama":
        story = OllamaStoryProvider(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.ollama_timeout_seconds,
            client=ollama_client,
        )
    else:
        raise ProviderConfigurationError(
            f"Unsupported STORY_PROVIDER '{story_provider}'. "
            "Choose mock, openai, anthropic, gemini, or ollama."
        )

    if settings.tts_provider == "mock":
        voice = MockVoiceProvider()
        voice_config = VoiceConfig(provider="mock", voice_id="default")
    elif settings.tts_provider == "offline":
        voice = OfflineTTSProvider()
        voice_config = VoiceConfig(provider="offline", voice_id="default")
    elif settings.tts_provider == "openai":
        _require(settings.openai_api_key, "OPENAI_API_KEY", settings.tts_provider)
        voice = OpenAIVoiceProvider(api_key=settings.openai_api_key)
        voice_config = VoiceConfig(
            provider="openai",
            voice_id=settings.openai_tts_voice,
            model=settings.openai_tts_model,
        )
    else:
        raise ProviderConfigurationError(
            f"Unsupported TTS_PROVIDER '{settings.tts_provider}'. "
            "Choose mock, offline, or openai."
        )

    return AIProviders(story, voice, voice_config)


def _require(value: str, variable: str, provider: str) -> None:
    if not value.strip():
        raise ProviderConfigurationError(
            f"{provider} provider selected but {variable} is not configured. "
            f"Set {variable} in .env, or select the mock provider."
        )
