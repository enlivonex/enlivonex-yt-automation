"""Voiceover generation providers."""

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from .models import VoiceConfig, VoiceSegment
from ..story.models import Story, StorySegment
from ..utils.ffmpeg import FFmpegWrapper


class VoiceProvider(ABC):
    """Base class for voiceover generation providers."""

    @abstractmethod
    def generate_voiceover(
        self,
        story: Story,
        output_dir: Path,
        voice_config: VoiceConfig
    ) -> List[VoiceSegment]:
        """Generate voiceover audio for a story.

        Args:
            story: Story object with segments
            output_dir: Directory to save audio files
            voice_config: Voice configuration

        Returns:
            List of VoiceSegment objects with audio paths
        """
        pass


class OpenAIVoiceProvider(VoiceProvider):
    """OpenAI TTS-based voiceover generation."""

    def __init__(
        self, api_key: str, client=None, ffmpeg: Optional[FFmpegWrapper] = None
    ):
        """Initialize OpenAI voice provider.

        Args:
            api_key: OpenAI API key
        """
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise RuntimeError(
                    "OpenAI TTS requires the optional 'openai' package. "
                    "Install it with: pip install -r requirements-ai.txt"
                ) from e
            client = OpenAI(api_key=api_key)
        self.client = client
        self.ffmpeg = ffmpeg or FFmpegWrapper()

    def generate_voiceover(
        self,
        story: Story,
        output_dir: Path,
        voice_config: VoiceConfig
    ) -> List[VoiceSegment]:
        """Generate voiceover using OpenAI TTS."""
        output_dir.mkdir(parents=True, exist_ok=True)

        voice_segments = []
        current_time = 0.0

        for i, segment in enumerate(story.segments):
            # Generate audio
            response = self.client.audio.speech.create(
                model=voice_config.model or "tts-1",
                voice=voice_config.voice_id,
                input=segment.text,
                speed=voice_config.speed
            )

            # Save audio file
            audio_path = output_dir / f"segment_{i:03d}.mp3"
            response.stream_to_file(str(audio_path))

            duration = self.ffmpeg.probe_duration(audio_path)

            voice_segments.append(VoiceSegment(
                text=segment.text,
                audio_path=audio_path,
                duration=duration,
                start_time=current_time,
                end_time=current_time + duration
            ))
            current_time += duration

        return voice_segments


class ElevenLabsVoiceProvider(VoiceProvider):
    """ElevenLabs-based voiceover generation."""

    def __init__(
        self, api_key: str, client=None, ffmpeg: Optional[FFmpegWrapper] = None
    ):
        """Initialize ElevenLabs voice provider.

        Args:
            api_key: ElevenLabs API key
        """
        if client is None:
            try:
                from elevenlabs import ElevenLabs
            except ImportError as e:
                raise RuntimeError(
                    "ElevenLabs TTS requires the optional 'elevenlabs' package. "
                    "Install it with: pip install -r requirements-ai.txt"
                ) from e
            client = ElevenLabs(api_key=api_key)
        self.client = client
        self.ffmpeg = ffmpeg or FFmpegWrapper()

    def generate_voiceover(
        self,
        story: Story,
        output_dir: Path,
        voice_config: VoiceConfig
    ) -> List[VoiceSegment]:
        """Generate voiceover using ElevenLabs."""
        output_dir.mkdir(parents=True, exist_ok=True)

        voice_segments = []
        current_time = 0.0

        for i, segment in enumerate(story.segments):
            # Generate audio
            audio_generator = self.client.text_to_speech.convert(
                voice_id=voice_config.voice_id,
                text=segment.text,
                model_id=voice_config.model or "eleven_multilingual_v2",
                voice_settings={
                    "stability": voice_config.stability,
                    "similarity_boost": voice_config.similarity_boost,
                }
            )

            # Save audio
            audio_path = output_dir / f"segment_{i:03d}.mp3"
            with open(audio_path, "wb") as f:
                for chunk in audio_generator:
                    f.write(chunk)

            duration = self.ffmpeg.probe_duration(audio_path)

            voice_segments.append(VoiceSegment(
                text=segment.text,
                audio_path=audio_path,
                duration=duration,
                start_time=current_time,
                end_time=current_time + duration
            ))
            current_time += duration

        return voice_segments


class MockVoiceProvider(VoiceProvider):
    """Mock voice provider for testing (no API required).
    
    Generates genuinely valid silent MP3 files using FFmpeg so the pipeline
    can complete real audio merging without breaking on empty files.
    """

    def generate_voiceover(
        self,
        story: Story,
        output_dir: Path,
        voice_config: VoiceConfig
    ) -> List[VoiceSegment]:
        """Generate mock voiceover files (valid silent MP3s)."""
        import subprocess
        from app.utils.process import hidden_process_creation_flags

        output_dir.mkdir(parents=True, exist_ok=True)

        voice_segments = []

        for i, segment in enumerate(story.segments):
            # Estimate duration
            words = len(segment.text.split())
            estimated_duration = max(1.0, (words / 150) * 60)

            # Create silent audio file using FFmpeg
            audio_path = output_dir / f"segment_{i:03d}.mp3"
            
            # Generate actual silent MP3 (valid frames)
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(estimated_duration),
                "-q:a", "9", "-acodec", "libmp3lame",
                str(audio_path)
            ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=hidden_process_creation_flags(),
            )

            voice_segments.append(VoiceSegment(
                text=segment.text,
                audio_path=audio_path,
                duration=estimated_duration,
                start_time=segment.start_time,
                end_time=segment.end_time
            ))

        return voice_segments
