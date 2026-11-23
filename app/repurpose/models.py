"""Typed data models shared by the local repurposing workflow."""

from dataclasses import dataclass
from pathlib import Path
import math


@dataclass(frozen=True)
class VideoSource:
    """Read-only description of a local source video."""

    path: Path
    sha256: str
    file_size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    aspect_ratio: str

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError("Video source path must be absolute.")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("Video source SHA-256 must be a lowercase hex digest.")
        if self.file_size_bytes <= 0:
            raise ValueError("Video source file size must be positive.")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("Video duration must be finite and positive.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Video dimensions must be positive.")
        if not math.isfinite(self.fps) or self.fps <= 0:
            raise ValueError("Video frame rate must be finite and positive.")


@dataclass(frozen=True)
class TranscriptSegment:
    """A transcript phrase and its time range in the source video."""

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.start_seconds)
            or not math.isfinite(self.end_seconds)
            or self.start_seconds < 0
            or self.end_seconds <= self.start_seconds
        ):
            raise ValueError("Transcript segment must have a valid positive range.")
        if not self.text.strip():
            raise ValueError("Transcript segment text cannot be empty.")


@dataclass(frozen=True)
class MomentCandidate:
    """A model-ranked, editable suggested range from a source video."""

    start_seconds: float
    end_seconds: float
    transcript: str
    score: float
    rationale: str

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.start_seconds)
            or not math.isfinite(self.end_seconds)
            or self.start_seconds < 0
            or self.end_seconds <= self.start_seconds
        ):
            raise ValueError("Moment candidate must have a valid positive range.")
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("Moment score must be between 0 and 1.")
        if not self.transcript.strip():
            raise ValueError("Moment transcript cannot be empty.")
        if not self.rationale.strip():
            raise ValueError("Moment rationale cannot be empty.")


@dataclass(frozen=True)
class ClipAnalysis:
    """Analysis result presented to a user for review before editing."""

    source: VideoSource
    transcript: tuple[TranscriptSegment, ...]
    candidates: tuple[MomentCandidate, ...]

    def __post_init__(self) -> None:
        previous_end = 0.0
        for segment in self.transcript:
            if segment.start_seconds < previous_end:
                raise ValueError("Transcript segments must be ordered and non-overlapping.")
            if segment.end_seconds > self.source.duration_seconds:
                raise ValueError("Transcript segment exceeds source duration.")
            previous_end = segment.end_seconds
        for candidate in self.candidates:
            if candidate.end_seconds > self.source.duration_seconds:
                raise ValueError("Moment candidate exceeds source duration.")
