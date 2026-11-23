"""Caption generator with timing from voiceover."""

from typing import List, Optional
from pathlib import Path

from .timed import CaptionWord, CaptionSegment, CaptionStyle, TimedCaptions
from ..voiceover.models import VoiceSegment


class TimedCaptionGenerator:
    """Generate timed captions from voiceover segments."""

    def __init__(self, style: Optional[CaptionStyle] = None):
        """Initialize caption generator.

        Args:
            style: Caption style (uses default if not provided)
        """
        self.style = style or CaptionStyle()

    def generate_from_voiceover(
        self,
        voice_segments: List[VoiceSegment],
        words_per_caption: int = 3
    ) -> TimedCaptions:
        """Generate timed captions from voiceover segments.

        Args:
            voice_segments: List of voiceover segments
            words_per_caption: Number of words to show per caption

        Returns:
            TimedCaptions object
        """
        caption_segments = []

        for voice_seg in voice_segments:
            # Split text into words
            words = voice_seg.text.split()
            if not words:
                continue

            # Estimate timing per word
            word_duration = voice_seg.duration / len(words)

            # Create timed words
            timed_words = []
            current_time = voice_seg.start_time

            for word in words:
                word_obj = CaptionWord(
                    word=word,
                    start_time=current_time,
                    end_time=current_time + word_duration
                )
                timed_words.append(word_obj)
                current_time += word_duration

            # Group words into caption segments
            for i in range(0, len(timed_words), words_per_caption):
                chunk = timed_words[i:i + words_per_caption]

                caption_seg = CaptionSegment(
                    words=chunk,
                    text=" ".join(w.word for w in chunk),
                    start_time=chunk[0].start_time,
                    end_time=chunk[-1].end_time
                )
                caption_segments.append(caption_seg)

        # Calculate total duration
        total_duration = max(
            (seg.end_time for seg in caption_segments),
            default=0.0
        )

        return TimedCaptions(
            segments=caption_segments,
            style=self.style,
            total_duration=total_duration
        )

    def generate_from_text(
        self,
        text: str,
        total_duration: float,
        words_per_caption: int = 3
    ) -> TimedCaptions:
        """Generate timed captions from plain text.

        Args:
            text: Text to caption
            total_duration: Total duration in seconds
            words_per_caption: Words per caption segment

        Returns:
            TimedCaptions object
        """
        words = text.split()
        if not words:
            return TimedCaptions([], self.style, 0.0)

        # Calculate time per word
        time_per_word = total_duration / len(words)

        # Create timed words
        timed_words = []
        current_time = 0.0

        for word in words:
            word_obj = CaptionWord(
                word=word,
                start_time=current_time,
                end_time=current_time + time_per_word
            )
            timed_words.append(word_obj)
            current_time += time_per_word

        # Group into segments
        caption_segments = []

        for i in range(0, len(timed_words), words_per_caption):
            chunk = timed_words[i:i + words_per_caption]

            caption_seg = CaptionSegment(
                words=chunk,
                text=" ".join(w.word for w in chunk),
                start_time=chunk[0].start_time,
                end_time=chunk[-1].end_time
            )
            caption_segments.append(caption_seg)

        return TimedCaptions(
            segments=caption_segments,
            style=self.style,
            total_duration=total_duration
        )
