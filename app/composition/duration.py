"""Duration planning and verification for narrated video composition."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DurationPlan:
    """Video adaptation required to cover the narration."""

    source_duration: float
    target_duration: float
    loop_video: bool


class DurationManager:
    """Choose trim/loop behavior and verify rendered duration."""

    def __init__(self, tolerance_seconds: float = 0.5):
        if tolerance_seconds < 0:
            raise ValueError("Duration tolerance must not be negative")
        self.tolerance_seconds = tolerance_seconds

    def plan(self, source_duration: float, target_duration: float) -> DurationPlan:
        """Plan source-video looping; output is always bounded to narration."""
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError("Source video duration must be a positive finite value")
        if not math.isfinite(target_duration) or target_duration <= 0:
            raise ValueError("Target narration duration must be a positive finite value")

        loop_video = source_duration + self.tolerance_seconds < target_duration
        return DurationPlan(source_duration, target_duration, loop_video)

    def verify(self, video_duration: float, audio_duration: float) -> None:
        """Raise if final video duration differs materially from narration."""
        if not math.isfinite(video_duration) or not math.isfinite(audio_duration):
            raise RuntimeError("Cannot verify non-finite media duration")
        if abs(video_duration - audio_duration) > self.tolerance_seconds:
            raise RuntimeError(
                "Composed video duration does not match narration: "
                f"video={video_duration:.3f}s, audio={audio_duration:.3f}s, "
                f"tolerance={self.tolerance_seconds:.3f}s"
            )
