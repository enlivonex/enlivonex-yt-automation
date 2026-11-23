"""Application service coordinating local video analysis."""

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
    ) -> ClipAnalysis:
        """Return transcript and ranked suggestions without changing the source."""
        source: VideoSource = self.catalog.inspect(video_path)
        transcript = self.transcriber.transcribe(source.path, language=language)
        candidates = self.moment_ranker.rank(
            transcript,
            source.duration_seconds,
            min_duration,
            max_duration,
            max_candidates,
        )
        return ClipAnalysis(source, transcript, candidates)
