"""Voiceover generation for YouTube Shorts."""

from .provider import (
    VoiceProvider, MockVoiceProvider, OpenAIVoiceProvider
)
from .offline_provider import OfflineTTSProvider
from .models import VoiceConfig, VoiceSegment

__all__ = [
    "VoiceProvider",
    "MockVoiceProvider",
    "OpenAIVoiceProvider",
    "OfflineTTSProvider",
    "VoiceConfig",
    "VoiceSegment"
]
