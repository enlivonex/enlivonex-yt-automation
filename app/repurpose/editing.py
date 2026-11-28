"""Editable clip selections and output settings for repurposing jobs."""

from dataclasses import dataclass
import math
from pathlib import Path

from .models import TranscriptSegment, VideoSource


@dataclass(frozen=True)
class ClipRange:
    """A user-selected source range, in seconds."""

    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.start_seconds)
            or not math.isfinite(self.end_seconds)
            or self.start_seconds < 0
            or self.end_seconds <= self.start_seconds
        ):
            raise ValueError("Clip range must have a valid positive time range.")


@dataclass(frozen=True)
class ClipOutputSettings:
    """Local video output settings shared by each rendered segment."""

    width: int = 1080
    height: int = 1920
    fps: int = 30
    fit_mode: str = "crop"
    quality_preset: str = "medium"
    burn_captions: bool = True
    caption_font_size: int = 48
    sharpness: float = 0.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    grain: int = 0
    creator_credit: str = ""
    caption_style: str = "standard"
    hardware_acceleration: str = "cpu"

    def __post_init__(self) -> None:
        if self.width < 2 or self.height < 2 or self.width % 2 or self.height % 2:
            raise ValueError("Output width and height must be positive even numbers.")
        if self.fps < 1 or self.fps > 120:
            raise ValueError("Output frame rate must be between 1 and 120.")
        if self.fit_mode not in {"crop", "pad", "blurred"}:
            raise ValueError("Fit mode must be 'crop', 'pad', or 'blurred'.")
        if self.quality_preset not in {"ultrafast", "fast", "medium", "slow"}:
            raise ValueError("Unsupported FFmpeg quality preset.")
        if self.caption_font_size < 8 or self.caption_font_size > 160:
            raise ValueError("Caption font size must be between 8 and 160.")
        adjustments = {
            "sharpness": (self.sharpness, 0.0, 1.5),
            "brightness": (self.brightness, -1.0, 1.0),
            "contrast": (self.contrast, 0.5, 1.5),
            "saturation": (self.saturation, 0.0, 2.0),
        }
        for name, (value, minimum, maximum) in adjustments.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{name.capitalize()} must be between {minimum:g} and "
                    f"{maximum:g}."
                )
        if (
            isinstance(self.grain, bool)
            or not isinstance(self.grain, int)
            or not 0 <= self.grain <= 20
        ):
            raise ValueError("Grain must be an integer between 0 and 20.")
        if (
            not isinstance(self.creator_credit, str)
            or len(self.creator_credit) > 100
            or any(ord(character) < 32 for character in self.creator_credit)
        ):
            raise ValueError("Creator credit must be a single line of at most 100 characters.")
        if self.caption_style not in {"standard", "gaming"}:
            raise ValueError("Caption style must be 'standard' or 'gaming'.")
        if self.hardware_acceleration not in {"auto", "cpu", "cuda"}:
            raise ValueError(
                "Hardware acceleration must be 'auto', 'cpu', or 'cuda'."
            )


@dataclass(frozen=True)
class ClipEditPlan:
    """An ordered set of moments compiled into one output video."""

    source: VideoSource
    ranges: tuple[ClipRange, ...]
    transcript: tuple[TranscriptSegment, ...] = ()
    settings: ClipOutputSettings = ClipOutputSettings()

    def __post_init__(self) -> None:
        if not self.ranges:
            raise ValueError("An edit plan must contain at least one clip range.")
        for clip_range in self.ranges:
            if clip_range.end_seconds > self.source.duration_seconds:
                raise ValueError("Clip range exceeds source video duration.")
        for segment in self.transcript:
            if segment.end_seconds > self.source.duration_seconds:
                raise ValueError("Transcript segment exceeds source video duration.")


@dataclass(frozen=True)
class RenderedClip:
    """Validated output-video details returned after successful rendering."""

    path: Path
    duration_seconds: float
    width: int
    height: int
    has_audio: bool
    source_range_count: int
