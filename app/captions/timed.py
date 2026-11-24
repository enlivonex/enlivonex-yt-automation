"""Timed caption models."""

from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path


@dataclass
class CaptionWord:
    """Single word with timing."""
    word: str
    start_time: float
    end_time: float


@dataclass
class CaptionSegment:
    """Caption segment (typically 1-3 words shown together)."""
    words: List[CaptionWord]
    text: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        """Get segment duration."""
        return self.end_time - self.start_time


@dataclass
class CaptionStyle:
    """Caption visual styling."""
    font_family: str = "Arial"
    font_size: int = 24
    font_color: str = "#FFFFFF"
    secondary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 2
    background_color: Optional[str] = None
    background_opacity: float = 0.0
    position_y: float = 0.5  # 0.0 = top, 1.0 = bottom
    max_width_percent: float = 0.9
    uppercase: bool = True
    bold: bool = True
    word_highlight: bool = False


@dataclass
class TimedCaptions:
    """Complete timed caption set."""
    segments: List[CaptionSegment]
    style: CaptionStyle
    total_duration: float

    def trim(self, max_duration: float) -> "TimedCaptions":
        """Return captions clipped so no word extends beyond max_duration."""
        if max_duration <= 0:
            raise ValueError("Caption trim duration must be positive")

        clipped_segments = []
        for segment in self.segments:
            if segment.start_time >= max_duration:
                continue
            words = [
                CaptionWord(
                    word=word.word,
                    start_time=word.start_time,
                    end_time=min(word.end_time, max_duration),
                )
                for word in segment.words
                if word.start_time < max_duration
            ]
            if not words:
                continue
            clipped_segments.append(
                CaptionSegment(
                    words=words,
                    text=" ".join(word.word for word in words),
                    start_time=words[0].start_time,
                    end_time=words[-1].end_time,
                )
            )
        return TimedCaptions(
            segments=clipped_segments,
            style=self.style,
            total_duration=min(self.total_duration, max_duration),
        )

    def to_srt(self) -> str:
        """Export as SRT format.

        Returns:
            SRT formatted string
        """
        lines = []
        for i, segment in enumerate(self.segments, 1):
            start = self._format_timestamp(segment.start_time)
            end = self._format_timestamp(segment.end_time)
            lines.append(f"{i}")
            lines.append(f"{start} --> {end}")
            lines.append(segment.text)
            lines.append("")  # Blank line

        return "\n".join(lines)

    def to_vtt(self) -> str:
        """Export as WebVTT format.

        Returns:
            WebVTT formatted string
        """
        lines = ["WEBVTT", ""]
        for segment in self.segments:
            start = self._format_timestamp(segment.start_time)
            end = self._format_timestamp(segment.end_time)
            lines.append(f"{start} --> {end}")
            lines.append(segment.text)
            lines.append("")

        return "\n".join(lines)

    def to_ass(self) -> str:
        """Export as ASS format with styling.

        Returns:
            ASS formatted string
        """
        # ASS header
        lines = [
            "[Script Info]",
            "Title: Shorts Captions",
            "ScriptType: v4.00+",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Outline, Alignment",
            f"Style: Default,{self.style.font_family},{self.style.font_size},{self._color_to_ass(self.style.font_color)},{self._color_to_ass(self.style.secondary_color)},{self._color_to_ass(self.style.outline_color)},{self._color_to_ass(self.style.background_color or '#00000000')},{'-1' if self.style.bold else '0'},{self.style.outline_width},2",
            "",
            "[Events]",
            "Format: Start, End, Style, Text"
        ]

        # Events
        for segment in self.segments:
            start = self._format_ass_timestamp(segment.start_time)
            end = self._format_ass_timestamp(segment.end_time)
            text = segment.text.upper() if self.style.uppercase else segment.text
            if self.style.word_highlight and segment.words:
                timed_words = []
                for index, word in enumerate(segment.words):
                    next_start = (
                        segment.words[index + 1].start_time
                        if index + 1 < len(segment.words)
                        else segment.end_time
                    )
                    duration_cs = max(
                        1, round((next_start - word.start_time) * 100)
                    )
                    word_text = word.word.replace("\\", "\\\\")
                    word_text = word_text.replace("{", "\\{").replace("}", "\\}")
                    timed_words.append(f"{{\\k{duration_cs}}}{word_text}")
                text = " ".join(timed_words)
            lines.append(f"Dialogue: {start},{end},Default,{text}")

        return "\n".join(lines)

    def save_srt(self, path: Path):
        """Save as SRT file."""
        path.write_text(self.to_srt(), encoding="utf-8")

    def save_vtt(self, path: Path):
        """Save as WebVTT file."""
        path.write_text(self.to_vtt(), encoding="utf-8")

    def save_ass(self, path: Path):
        """Save as ASS file."""
        path.write_text(self.to_ass(), encoding="utf-8")

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format timestamp for SRT/VTT (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _format_ass_timestamp(seconds: float) -> str:
        """Format timestamp for ASS (H:MM:SS.cc)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:05.2f}"

    @staticmethod
    def _color_to_ass(hex_color: str) -> str:
        """Convert hex color to ASS format (&HAABBGGRR)."""
        if not hex_color.startswith("#"):
            return "&H00FFFFFF"

        hex_color = hex_color.lstrip("#")

        if len(hex_color) == 6:
            r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
            return f"&H00{b}{g}{r}"
        elif len(hex_color) == 8:
            r, g, b, a = hex_color[0:2], hex_color[2:4], hex_color[4:6], hex_color[6:8]
            return f"&H{a}{b}{g}{r}"

        return "&H00FFFFFF"
