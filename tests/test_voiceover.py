"""Tests for voiceover generation."""

import pytest
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
from app.voiceover.models import VoiceConfig, VoiceSegment
from app.voiceover.provider import (
    MockVoiceProvider,
    OpenAIVoiceProvider,
    VoiceProvider,
)
from app.voiceover.offline_provider import OfflineTTSProvider
from app.story.models import Story, StorySegment


@pytest.fixture
def sample_story():
    """Create sample story for testing."""
    segments = [
        StorySegment("This is the hook.", 0.0, 3.0, "excited"),
        StorySegment("This is the main content.", 3.0, 55.0, None),
        StorySegment("This is the outro.", 55.0, 60.0, "friendly")
    ]

    return Story(
        title="Test Story",
        hook="This is the hook.",
        segments=segments,
        total_duration=60.0,
        tone="casual",
        target_audience="general"
    )


@pytest.fixture
def voice_config():
    """Create sample voice config."""
    return VoiceConfig(
        provider="mock",
        voice_id="test-voice",
        speed=1.0
    )


def test_voice_config_creation():
    """Test creating voice config."""
    config = VoiceConfig(
        provider="openai",
        voice_id="alloy",
        model="tts-1",
        speed=1.1
    )

    assert config.provider == "openai"
    assert config.voice_id == "alloy"
    assert config.model == "tts-1"
    assert config.speed == 1.1


def test_voice_segment_creation():
    """Test creating voice segment."""
    segment = VoiceSegment(
        text="Test text",
        audio_path=Path("/tmp/test.mp3"),
        duration=5.0,
        start_time=0.0,
        end_time=5.0
    )

    assert segment.text == "Test text"
    assert segment.duration == 5.0
    assert segment.start_time == 0.0


def test_mock_provider_generates_files(sample_story, voice_config, tmp_path):
    """Test mock provider creates audio files."""
    provider = MockVoiceProvider()

    output_dir = tmp_path / "voiceovers"
    segments = provider.generate_voiceover(sample_story, output_dir, voice_config)

    assert len(segments) == 3
    assert all(seg.audio_path.exists() for seg in segments)
    assert segments[0].text == "This is the hook."
    assert segments[0].start_time == 0.0


def test_mock_provider_creates_output_dir(sample_story, voice_config, tmp_path):
    """Test mock provider creates output directory if missing."""
    provider = MockVoiceProvider()

    output_dir = tmp_path / "new_dir" / "voiceovers"
    assert not output_dir.exists()

    segments = provider.generate_voiceover(sample_story, output_dir, voice_config)

    assert output_dir.exists()
    assert len(segments) == 3


def test_mock_provider_duration_estimation(sample_story, voice_config, tmp_path):
    """Test duration estimation."""
    provider = MockVoiceProvider()

    output_dir = tmp_path / "voiceovers"
    segments = provider.generate_voiceover(sample_story, output_dir, voice_config)

    # All segments should have positive duration
    assert all(seg.duration > 0 for seg in segments)

    # Longer text should have longer duration
    assert segments[1].duration > segments[0].duration


def test_openai_tts_uses_measured_audio_duration_and_sequential_timing(
    sample_story, tmp_path
):
    client = Mock()
    client.audio.speech.create.return_value.stream_to_file.side_effect = (
        lambda path: Path(path).write_bytes(b"mocked encoded speech")
    )
    ffmpeg = Mock()
    ffmpeg.probe_duration.side_effect = [2.4, 4.7, 1.6]
    provider = OpenAIVoiceProvider("test-key", client=client, ffmpeg=ffmpeg)

    segments = provider.generate_voiceover(
        sample_story,
        tmp_path / "voice",
        VoiceConfig(provider="openai", voice_id="alloy", model="tts-1"),
    )

    assert [segment.duration for segment in segments] == [2.4, 4.7, 1.6]
    assert [(segment.start_time, segment.end_time) for segment in segments] == [
        (0.0, 2.4), (2.4, 7.1), (7.1, 8.7)
    ]
    assert all(segment.audio_path.exists() for segment in segments)
    assert client.audio.speech.create.call_count == 3
    assert client.audio.speech.create.call_args.kwargs["model"] == "tts-1"
    assert client.audio.speech.create.call_args.kwargs["voice"] == "alloy"


def test_offline_tts_uses_existing_provider_interface_and_measured_durations(
    monkeypatch, sample_story, tmp_path
):
    engine = Mock()
    pending_paths = []

    def convert_audio(args):
        Path(args[-1]).write_bytes(b"mp3 speech")
        return 0, "", ""

    def complete_writes():
        for path in pending_paths:
            Path(path).write_bytes(b"wav audio")

    engine.save_to_file.side_effect = (
        lambda _text, path: pending_paths.append(path)
    )
    engine.runAndWait.side_effect = complete_writes
    engine.getProperty.return_value = []
    ffmpeg = Mock()
    ffmpeg.execute.side_effect = convert_audio
    ffmpeg.probe_duration.side_effect = [1.2, 2.5, 0.8]
    monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(init=lambda: engine))

    provider = OfflineTTSProvider(ffmpeg=ffmpeg)
    segments = provider.generate_voiceover(
        sample_story,
        tmp_path / "offline_voice",
        VoiceConfig(provider="offline", voice_id="default"),
    )

    assert isinstance(provider, VoiceProvider)
    assert [segment.duration for segment in segments] == [1.2, 2.5, 0.8]
    assert [(segment.start_time, segment.end_time) for segment in segments] == [
        (0.0, 1.2), (1.2, 3.7), (3.7, 4.5),
    ]
    assert all(segment.audio_path.exists() for segment in segments)
    assert engine.save_to_file.call_count == 3
    engine.runAndWait.assert_called_once()
