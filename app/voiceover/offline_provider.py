"""
Offline TTS Provider using pyttsx3 (free, offline, Windows SAPI voices).
Zero cost, no API keys, works entirely offline.
"""
from pathlib import Path
from typing import List
from ..voiceover.models import VoiceConfig, VoiceSegment
from ..story.models import Story
from ..utils.ffmpeg import FFmpegWrapper
from .provider import VoiceProvider


class OfflineTTSProvider(VoiceProvider):
    """Free offline text-to-speech using pyttsx3 (Windows SAPI)."""

    def __init__(self, ffmpeg=None):
        """Initialize offline TTS provider.

        Uses pyttsx3 which wraps Windows SAPI voices (built into Windows).
        No API keys, no internet connection, zero cost.
        """
        try:
            import pyttsx3
        except ImportError:
            raise RuntimeError(
                "Offline TTS requires pyttsx3. Install with: pip install pyttsx3"
            )

        self.engine = pyttsx3.init()
        self.ffmpeg = ffmpeg or FFmpegWrapper()

        # Configure voice properties
        self.engine.setProperty('rate', 165)  # Speed (words per minute)
        self.engine.setProperty('volume', 1.0)  # Volume (0.0 to 1.0)

        # Try to use a better voice if available
        voices = self.engine.getProperty('voices')
        if voices:
            # Prefer female voice for narration (usually index 1)
            if len(voices) > 1:
                self.engine.setProperty('voice', voices[1].id)

    def generate_voiceover(
        self,
        story: Story,
        output_dir: Path,
        voice_config: VoiceConfig
    ) -> List[VoiceSegment]:
        """Generate voiceover audio for a story using offline TTS.

        Args:
            story: Story object with segments
            output_dir: Directory to save audio files
            voice_config: Voice configuration (provider field indicates offline)

        Returns:
            List of VoiceSegment objects with audio paths and measured durations
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        voice_segments = []
        current_time = 0.0
        pending_segments = []

        for i, segment in enumerate(story.segments):
            # Generate audio file
            audio_path = output_dir / f"segment_{i:03d}.wav"
            self.engine.save_to_file(segment.text, str(audio_path))
            pending_segments.append((segment, audio_path))

        if pending_segments:
            self.engine.runAndWait()

        for segment, audio_path in pending_segments:
            mp3_path = output_dir / f"segment_{len(voice_segments):03d}.mp3"
            returncode, _, stderr = self.ffmpeg.execute([
                "-i", str(audio_path),
                "-codec:a", "libmp3lame",
                "-q:a", "2",
                "-y",
                str(mp3_path)
            ])

            if returncode != 0:
                raise RuntimeError(f"Failed to convert audio to MP3: {stderr}")

            # Remove WAV file
            audio_path.unlink()

            # Measure actual duration using FFprobe
            duration = self.ffmpeg.probe_duration(mp3_path)

            voice_segments.append(VoiceSegment(
                text=segment.text,
                audio_path=mp3_path,
                duration=duration,
                start_time=current_time,
                end_time=current_time + duration
            ))

            current_time += duration

        return voice_segments
