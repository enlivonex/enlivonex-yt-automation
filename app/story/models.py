"""Story data models."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StorySegment:
    """Single segment of a story."""
    text: str
    start_time: float  # seconds
    end_time: float    # seconds
    emphasis: Optional[str] = None  # emotion/tone hint


@dataclass
class Story:
    """Complete story for a Short."""
    title: str
    hook: str  # Opening line
    segments: List[StorySegment]
    total_duration: float  # seconds
    tone: str  # casual, dramatic, educational, etc.
    target_audience: Optional[str] = None

    @property
    def full_text(self) -> str:
        """Get complete story text."""
        return "\n".join(seg.text for seg in self.segments)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "hook": self.hook,
            "segments": [
                {
                    "text": seg.text,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "emphasis": seg.emphasis
                }
                for seg in self.segments
            ],
            "total_duration": self.total_duration,
            "tone": self.tone,
            "target_audience": self.target_audience
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Story":
        """Create from dictionary."""
        return cls(
            title=data["title"],
            hook=data["hook"],
            segments=[
                StorySegment(
                    text=seg["text"],
                    start_time=seg["start_time"],
                    end_time=seg["end_time"],
                    emphasis=seg.get("emphasis")
                )
                for seg in data["segments"]
            ],
            total_duration=data["total_duration"],
            tone=data["tone"],
            target_audience=data.get("target_audience")
        )
