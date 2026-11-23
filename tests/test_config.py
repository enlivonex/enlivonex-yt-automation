"""Tests for configuration module."""

import os
import tempfile
from pathlib import Path
import pytest
from app.config import Settings, load_settings
from app.config.ai_providers import (
    ProviderConfigurationError,
    create_ai_providers,
)
from app.story.provider import GeminiStoryProvider, TemplateStoryProvider
from app.voiceover.provider import MockVoiceProvider, OpenAIVoiceProvider


def test_settings_defaults():
    """Test default settings values."""
    settings = Settings()

    assert settings.dry_run is True
    assert settings.youtube_privacy_status == "private"
    assert settings.caption_max_length > 0
    assert settings.caption_font_size > 0
    assert settings.tts_provider == "offline"


def test_settings_validation():
    """Test settings validation."""
    settings = Settings()
    errors = settings.validate()

    # Should have minimal errors with defaults
    assert isinstance(errors, list)


def test_settings_invalid_caption_length():
    """Test validation catches invalid caption length."""
    os.environ["CAPTION_MAX_LENGTH"] = "0"
    settings = Settings()
    errors = settings.validate()

    assert any("CAPTION_MAX_LENGTH" in error for error in errors)

    # Cleanup
    del os.environ["CAPTION_MAX_LENGTH"]


def test_settings_invalid_privacy_status():
    """Test validation catches invalid privacy status."""
    os.environ["YOUTUBE_PRIVACY_STATUS"] = "invalid"
    settings = Settings()
    errors = settings.validate()

    assert any("YOUTUBE_PRIVACY_STATUS" in error for error in errors)

    # Cleanup
    del os.environ["YOUTUBE_PRIVACY_STATUS"]


def test_rgba_color_parsing():
    """Test RGBA color parsing."""
    settings = Settings()

    # Test valid color
    assert settings._parse_rgba("255,128,64,200") == (255, 128, 64, 200)

    # Test invalid color returns default
    assert settings._parse_rgba("invalid") == (0, 0, 0, 200)
    assert settings._parse_rgba("255,128") == (0, 0, 0, 200)


def test_google_configured():
    """Test Google configuration detection."""
    # Not configured
    settings = Settings()
    assert not settings.is_google_configured()

    # Configured
    os.environ["GOOGLE_CLIENT_ID"] = "test_id"
    os.environ["GOOGLE_CLIENT_SECRET"] = "test_secret"
    os.environ["GOOGLE_DRIVE_FOLDER_ID"] = "test_folder"

    settings = Settings()
    assert settings.is_google_configured()

    # Cleanup
    del os.environ["GOOGLE_CLIENT_ID"]
    del os.environ["GOOGLE_CLIENT_SECRET"]
    del os.environ["GOOGLE_DRIVE_FOLDER_ID"]


def test_youtube_configured(monkeypatch, tmp_path):
    """Test YouTube configuration detection."""
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET_FILE", str(tmp_path / "missing_secret.json"))
    settings = Settings()

    # Not configured (file doesn't exist)
    assert not settings.is_youtube_configured()

    # Configured (file exists)
    secret_file = tmp_path / "present_secret.json"
    secret_file.touch()
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET_FILE", str(secret_file))
    settings = Settings()
    assert settings.is_youtube_configured()


def test_load_settings_from_file():
    """Test loading settings from env file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("DRY_RUN=false\n")
        f.write("CAPTION_STYLE=funny\n")
        env_file = f.name

    try:
        settings = load_settings(env_file)
        assert settings.dry_run is False
        assert settings.caption_style == "funny"
    finally:
        os.unlink(env_file)


def test_ai_provider_status_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")

    status = Settings().ai_provider_status()

    assert status == {"story": "configured", "tts": "configured"}
    assert "secret-test-value" not in repr(status)


def test_ai_providers_default_to_template_and_offline_tts(monkeypatch):
    monkeypatch.delenv("STORY_PROVIDER", raising=False)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    from app.config import ai_providers
    offline_provider = object()
    monkeypatch.setattr(
        ai_providers, "OfflineTTSProvider", lambda: offline_provider
    )
    providers = create_ai_providers(Settings())

    assert isinstance(providers.story, TemplateStoryProvider)
    assert providers.voice is offline_provider
    assert providers.voice_config.provider == "offline"


def test_openai_tts_requires_credentials_before_job_creation(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "mock")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings()

    assert settings.ai_provider_status()["tts"] == "not configured"
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        create_ai_providers(settings)


def test_openai_provider_selection_uses_configured_model_and_voice(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "mock")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_TTS_MODEL", "tts-test")
    monkeypatch.setenv("OPENAI_TTS_VOICE", "nova")
    settings = Settings()

    # Prevent SDK initialization/network access while validating provider wiring.
    from app.config import ai_providers
    monkeypatch.setattr(ai_providers, "OpenAIVoiceProvider", lambda api_key: MockVoiceProvider())
    providers = create_ai_providers(settings)

    assert isinstance(providers.voice, MockVoiceProvider)
    assert providers.voice_config.model == "tts-test"
    assert providers.voice_config.voice_id == "nova"


def test_gemini_provider_selection_uses_configured_model(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "gemini")
    monkeypatch.setenv("TTS_PROVIDER", "mock")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_STORY_MODEL", "gemini-test")

    providers = create_ai_providers(Settings())

    assert isinstance(providers.story, GeminiStoryProvider)
    assert providers.story.model == "gemini-test"
    assert isinstance(providers.voice, MockVoiceProvider)


def test_gemini_provider_selection_requires_api_key(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = Settings()

    assert settings.ai_provider_status()["story"] == "not configured"
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
        create_ai_providers(settings)


def test_gemini_model_default_is_current_flash_model(monkeypatch):
    monkeypatch.delenv("GEMINI_STORY_MODEL", raising=False)

    assert Settings().gemini_story_model == "gemini-3.8-flash"


def test_ollama_provider_uses_configured_local_model(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    providers = create_ai_providers(Settings())

    assert providers.story.model == "qwen2.5:3b"
    assert providers.story.client.base_url == "http://127.0.0.1:11434"
