"""Application service coordinating local video analysis."""

from collections.abc import Callable
from pathlib import Path

from .models import ClipAnalysis, VideoSource
from .moments import MomentRanker
from .source import LocalVideoCatalog
from .transcription import Transcriber


class LocalClipAnalyzer:
    """Inspect, transcribe, and suggest reviewable moments from a local video."""

    def __init__(
        self,
        catalog: LocalVideoCatalog,
        transcriber: Transcriber,
        moment_ranker: MomentRanker,
    ):
        self.catalog = catalog
        self.transcriber = transcriber
        self.moment_ranker = moment_ranker

    def analyze(
        self,
        video_path: Path,
        *,
        min_duration: float = 15,
        max_duration: float = 60,
        max_candidates: int = 8,
        language: str | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> ClipAnalysis:
        """Return transcript and ranked suggestions without changing the source."""
        if progress_callback is not None:
            progress_callback("Inspecting source video...")
        source: VideoSource = self.catalog.inspect(video_path)
        if progress_callback is not None:
            progress_callback("Transcribing with the local speech model...")
        transcript = self.transcriber.transcribe(source.path, language=language)
        if progress_callback is not None:
            progress_callback("Ranking moments with the local Ollama model...")
        candidates = self.moment_ranker.rank(
            transcript,
            source.duration_seconds,
            min_duration,
            max_duration,
            max_candidates,
        )
        return ClipAnalysis(source, transcript, candidates)
