"""Application service coordinating local video analysis."""

from collections.abc import Callable
from pathlib import Path
import tempfile
from threading import Event

from .cancellation import raise_if_cancelled
from ..utils.ffmpeg import FFmpegWrapper
from .editing import (
    ClipEditPlan,
    ClipOutputSettings,
    ClipRange,
    RenderedClip,
)
from .models import ClipAnalysis, TranscriptSegment, TranscriptWord, VideoSource
from .moments import MomentRanker
from .renderer import LocalClipRenderer
from .source import LocalVideoCatalog
from .transcription import Transcriber

MAX_MANUAL_CLIP_SECONDS = 90


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
        progress_fraction_callback: Callable[[float], None] | None = None,
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
        if progress_fraction_callback is not None:
            progress_fraction_callback(0.05)
        if progress_callback is not None:
            progress_callback("Transcribing with the local speech model...")
        if progress_fraction_callback is None:
            if cancel_event is None:
                transcript = self.transcriber.transcribe(
                    source.path, language=language
                )
            else:
                transcript = self.transcriber.transcribe(
                    source.path,
                    language=language,
                    cancel_event=cancel_event,
                )
        else:
            def report_transcription_progress(fraction: float) -> None:
                progress_fraction_callback(0.05 + 0.75 * fraction)

            transcript = self.transcriber.transcribe(
                source.path,
                language=language,
                cancel_event=cancel_event,
                progress_callback=report_transcription_progress,
                progress_duration_seconds=source.duration_seconds,
            )
        raise_if_cancelled(cancel_event)
        if progress_fraction_callback is not None:
            progress_fraction_callback(0.85)
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
        if progress_fraction_callback is not None:
            progress_fraction_callback(1.0)
        return ClipAnalysis(source, transcript, candidates)


class LocalManualClipper:
    """Transcribe captions for and render one exact source-video range."""

    def __init__(
        self,
        ffmpeg: FFmpegWrapper | None = None,
        catalog: LocalVideoCatalog | None = None,
        renderer: LocalClipRenderer | None = None,
    ):
        self.ffmpeg = ffmpeg or FFmpegWrapper()
        self.catalog = catalog or LocalVideoCatalog(self.ffmpeg)
        self.renderer = renderer or LocalClipRenderer(
            self.ffmpeg, self.catalog
        )

    def render(
        self,
        video_path: Path,
        start_seconds: float,
        end_seconds: float,
        output_path: Path,
        settings: ClipOutputSettings,
        *,
        transcriber: Transcriber | None = None,
        language: str | None = None,
        cancel_event: Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> RenderedClip:
        raise_if_cancelled(cancel_event)
        clip_range = ClipRange(start_seconds, end_seconds)
        duration = clip_range.end_seconds - clip_range.start_seconds
        if duration > MAX_MANUAL_CLIP_SECONDS:
            raise ValueError("Manual clip length must not exceed 90 seconds.")
        if settings.burn_captions and transcriber is None:
            raise ValueError(
                "A local transcriber is required when captions are enabled."
            )

        if stage_callback is not None:
            stage_callback("Inspecting source video...")
        if cancel_event is None:
            source = self.catalog.inspect(video_path)
        else:
            source = self.catalog.inspect(video_path, cancel_event=cancel_event)
        if clip_range.end_seconds > source.duration_seconds:
            raise ValueError("Clip end time exceeds source video duration.")

        destination = Path(output_path).expanduser().resolve()
        if destination == source.path:
            raise ValueError("Output video must not overwrite the source video.")
        if destination.suffix.lower() != ".mp4":
            raise ValueError("Rendered output path must use the .mp4 extension.")
        if destination.exists():
            raise FileExistsError(f"Output video already exists: {destination}")

        transcript: tuple[TranscriptSegment, ...] = ()
        if settings.burn_captions:
            if stage_callback is not None:
                stage_callback("Extracting audio from the selected range...")
            with tempfile.TemporaryDirectory(
                prefix="enlivonex-clip-audio-"
            ) as folder:
                audio_path = Path(folder) / "selected-range.wav"
                args = [
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{clip_range.start_seconds:.6f}",
                    "-i",
                    str(source.path),
                    "-t",
                    f"{duration:.6f}",
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(audio_path),
                ]
                if cancel_event is None:
                    return_code, _, stderr = self.ffmpeg.execute(
                        args, timeout=300
                    )
                else:
                    extraction_progress = None
                    if progress_callback is not None:
                        extraction_progress = lambda fraction: (
                            progress_callback(0.1 * fraction)
                        )
                    return_code, _, stderr = self.ffmpeg.execute_cancellable(
                        args,
                        expected_duration_seconds=duration,
                        cancel_event=cancel_event,
                        progress_callback=extraction_progress,
                        timeout=300,
                    )
                if return_code != 0:
                    detail = stderr.strip()[-1200:] or (
                        "FFmpeg returned a non-zero exit code."
                    )
                    raise RuntimeError(
                        f"Could not extract selected-range audio: {detail}"
                    )
                if not audio_path.is_file() or audio_path.stat().st_size == 0:
                    raise RuntimeError(
                        "FFmpeg did not create audio for the selected range."
                    )
                raise_if_cancelled(cancel_event)
                if stage_callback is not None:
                    stage_callback("Transcribing the selected range for captions...")
                transcript = _offset_transcript(
                    transcriber.transcribe(
                        audio_path,
                        language=language,
                        cancel_event=cancel_event,
                    ),
                    clip_range.start_seconds,
                )

        raise_if_cancelled(cancel_event)
        if stage_callback is not None:
            stage_callback("Rendering the exact clip...")
        render_progress = None
        if progress_callback is not None:
            render_progress = lambda fraction: progress_callback(
                0.1 + 0.9 * fraction
            )
        return self.renderer.render(
            ClipEditPlan(
                source=source,
                ranges=(clip_range,),
                transcript=transcript,
                settings=settings,
            ),
            destination,
            cancel_event=cancel_event,
            progress_callback=render_progress,
            stage_callback=stage_callback,
        )


def _offset_transcript(
    transcript: tuple[TranscriptSegment, ...], offset_seconds: float
) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(
            start_seconds=segment.start_seconds + offset_seconds,
            end_seconds=segment.end_seconds + offset_seconds,
            text=segment.text,
            words=tuple(
                TranscriptWord(
                    start_seconds=word.start_seconds + offset_seconds,
                    end_seconds=word.end_seconds + offset_seconds,
                    text=word.text,
                )
                for word in segment.words
            ),
        )
        for segment in transcript
    )
