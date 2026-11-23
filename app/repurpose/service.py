"""Application service coordinating local video analysis."""

from collections.abc import Callable
from pathlib import Path
from threading import Event

from .cancellation import raise_if_cancelled
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
        cancel_event: Event | None = None,
    ) -> ClipAnalysis:
        """Return transcript and ranked suggestions without changing the source."""
        raise_if_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback("Inspecting source video...")
        if cancel_event is None:
            source: VideoSource = self.catalog.inspect(video_path)
        else:
            source = self.catalog.inspect(video_path, cancel_event=cancel_event)
        raise_if_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback("Transcribing with the local speech model...")
        if cancel_event is None:
            transcript = self.transcriber.transcribe(
                source.path, language=language
            )
        else:
            transcript = self.transcriber.transcribe(
                source.path, language=language, cancel_event=cancel_event
            )
        raise_if_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback("Ranking moments with the local Ollama model...")
        rank_options = (
            transcript,
            source.duration_seconds,
            min_duration,
            max_duration,
            max_candidates,
        )
        if cancel_event is None:
            candidates = self.moment_ranker.rank(*rank_options)
        else:
            candidates = self.moment_ranker.rank(
                *rank_options, cancel_event=cancel_event
            )
        raise_if_cancelled(cancel_event)
        return ClipAnalysis(source, transcript, candidates)
