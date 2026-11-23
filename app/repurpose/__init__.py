"""Backend building blocks for local long-video repurposing."""

from .models import (
    ClipAnalysis,
    MomentCandidate,
    TranscriptSegment,
    VideoSource,
)
from .editing import ClipEditPlan, ClipOutputSettings, ClipRange, RenderedClip
from .moments import MomentRanker, OllamaMomentRanker
from .renderer import LocalClipRenderer
from .service import LocalClipAnalyzer
from .source import LocalVideoCatalog, VideoScanFailure, VideoScanResult
from .transcription import FasterWhisperTranscriber, Transcriber

__all__ = [
    "ClipAnalysis",
    "ClipEditPlan",
    "ClipOutputSettings",
    "ClipRange",
    "FasterWhisperTranscriber",
    "LocalClipAnalyzer",
    "LocalVideoCatalog",
    "LocalClipRenderer",
    "MomentRanker",
    "MomentCandidate",
    "OllamaMomentRanker",
    "TranscriptSegment",
    "Transcriber",
    "RenderedClip",
    "VideoScanFailure",
    "VideoScanResult",
    "VideoSource",
]
