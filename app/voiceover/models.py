"""Voiceover data models."""

from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class VoiceConfig:
    """Voice configuration."""
    provider: str  # openai, elevenlabs, etc.
    voice_id: str  # Provider-specific voice identifier
    model: Optional[str] = None
    speed: float = 1.0
    pitch: float = 1.0
    stability: float = 0.5  # ElevenLabs only
    similarity_boost: float = 0.75  # ElevenLabs only


@dataclass
class VoiceSegment:
    """Single voiceover audio segment."""
    text: str
    audio_path: Path
    duration: float
    start_time: float
    end_time: float
