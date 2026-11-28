"""Terminal workflow for analyzing local videos and rendering selected moments."""

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

from ..ai.ollama import OllamaClient
from .editing import ClipEditPlan, ClipOutputSettings, ClipRange
from .models import (
    ClipAnalysis,
    MomentCandidate,
    TranscriptSegment,
    TranscriptWord,
    VideoSource,
)
from .moments import OllamaMomentRanker
from .renderer import LocalClipRenderer
from .service import (
    MAX_MANUAL_CLIP_SECONDS,
    LocalClipAnalyzer,
    LocalManualClipper,
)
from .source import LocalVideoCatalog
from .transcription import FasterWhisperTranscriber

ANALYSIS_SCHEMA_VERSION = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze local videos and render selected clips using local AI."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser(
        "analyze", help="Transcribe a local video and suggest moments."
    )
    analyze.add_argument("video", type=Path, help="Local source video.")
    analyze.add_argument(
        "--whisper-model",
        type=Path,
        required=True,
        help="Path to an already downloaded Faster-Whisper model directory.",
    )
    analyze.add_argument(
        "--ollama-model",
        default="qwen2.5:3b",
        help="Installed local Ollama model used to rank transcript moments.",
    )
    analyze.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Local Ollama server URL.",
    )
    analyze.add_argument("--language", help="Optional speech language code.")
    analyze.add_argument("--min-duration", type=float, default=15)
    analyze.add_argument("--max-duration", type=float, default=60)
    analyze.add_argument("--max-candidates", type=int, default=8)
    analyze.add_argument(
        "--analysis-output",
        type=Path,
        help="JSON file to save the transcript and suggested moments.",
    )

    render = commands.add_parser(
        "render", help="Render selected moment numbers into one MP4."
    )
    render.add_argument("analysis", type=Path, help="Analysis JSON from analyze.")
    render.add_argument(
        "--candidate",
        type=int,
        action="append",
        required=True,
        help="1-based candidate number; repeat to join multiple moments.",
    )
    _add_output_options(render)

    clip = commands.add_parser(
        "clip",
        help="Render an exact source range, up to 90 seconds, with local captions.",
    )
    clip.add_argument("video", type=Path, help="Local source video.")
    clip.add_argument(
        "--start",
        type=_parse_time,
        required=True,
        help="Clip start: seconds, MM:SS, or HH:MM:SS.",
    )
    clip.add_argument(
        "--end",
        type=_parse_time,
        required=True,
        help="Clip end: seconds, MM:SS, or HH:MM:SS; maximum clip length is 90s.",
    )
    clip.add_argument(
        "--whisper-model",
        type=Path,
        help="Path to an already downloaded Faster-Whisper model directory.",
    )
    clip.add_argument("--language", help="Optional speech language code.")
    _add_output_options(clip)
    return parser


def _add_output_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--width", type=int, default=1080)
    command.add_argument("--height", type=int, default=1920)
    command.add_argument("--fps", type=int, default=30)
    command.add_argument(
        "--fit-mode",
        choices=("crop", "pad", "blurred"),
        default="crop",
    )
    command.add_argument(
        "--caption-style",
        choices=("standard", "gaming"),
        default="standard",
    )
    command.add_argument(
        "--sharpness",
        type=float,
        default=0.0,
        help="Creative sharpness adjustment from 0 to 1.5 (default: 0).",
    )
    command.add_argument(
        "--contrast",
        type=float,
        default=1.0,
        help="Creative contrast adjustment from 0.5 to 1.5 (default: 1).",
    )
    command.add_argument(
        "--saturation",
        type=float,
        default=1.0,
        help="Creative saturation adjustment from 0 to 2 (default: 1).",
    )
    command.add_argument(
        "--grain",
        type=int,
        default=0,
        help="Creative grain strength from 0 to 20 (default: 0).",
    )
    command.add_argument(
        "--quality-preset",
        choices=("ultrafast", "fast", "medium", "slow"),
        default="medium",
    )
    command.add_argument("--caption-font-size", type=int, default=48)
    command.add_argument(
        "--creator-credit",
        default="",
        help="Optional creator name to show as a credit at the top of each clip.",
    )
    command.add_argument(
        "--no-captions",
        action="store_true",
        help="Do not burn captions; exact clips also skip transcription.",
    )


def _parse_time(value: str) -> float:
    parts = value.strip().split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            minutes = int(parts[0])
            remainder = float(parts[1])
            if minutes < 0 or not 0 <= remainder < 60:
                raise ValueError
            seconds = minutes * 60 + remainder
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            remainder = float(parts[2])
            if hours < 0 or not 0 <= minutes < 60 or not 0 <= remainder < 60:
                raise ValueError
            seconds = hours * 3600 + minutes * 60 + remainder
        else:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Time must be seconds, MM:SS, or HH:MM:SS."
        ) from error
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("Time must be finite and non-negative.")
    return seconds


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            _analyze(args)
        elif args.command == "render":
            _render(args)
        else:
            _clip(args)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


def _analyze(args: argparse.Namespace) -> None:
    video_path = args.video.expanduser().resolve()
    analysis_path = args.analysis_output or video_path.with_suffix(
        ".analysis.json"
    )
    analysis_path = analysis_path.expanduser().resolve()
    if analysis_path.exists():
        raise FileExistsError(f"Analysis file already exists: {analysis_path}")

    analyzer = LocalClipAnalyzer(
        catalog=LocalVideoCatalog(),
        transcriber=FasterWhisperTranscriber(args.whisper_model, device="cpu"),
        moment_ranker=OllamaMomentRanker(
            OllamaClient(
                base_url=args.ollama_url,
                model=args.ollama_model,
                cpu_only=True,
            )
        ),
    )
    result = analyzer.analyze(
        video_path,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        max_candidates=args.max_candidates,
        language=args.language,
    )
    _write_analysis(analysis_path, result)
    print(f"Analysis saved: {analysis_path}")
    print(f"Transcript segments: {len(result.transcript)}")
    if not result.candidates:
        print("No candidate moments met the selected duration range.")
    for index, candidate in enumerate(result.candidates, 1):
        print(
            f"{index}. {candidate.start_seconds:.2f}-{candidate.end_seconds:.2f}s "
            f"score={candidate.score:.2f} — {candidate.rationale}"
        )
        print(f"   {candidate.transcript}")


def _render(args: argparse.Namespace) -> None:
    analysis = _read_analysis(args.analysis.expanduser().resolve())
    if len(set(args.candidate)) != len(args.candidate):
        raise ValueError("Candidate numbers must not be repeated.")
    selected = []
    for number in args.candidate:
        if number < 1 or number > len(analysis.candidates):
            raise ValueError(
                f"Candidate {number} is out of range; this analysis has "
                f"{len(analysis.candidates)} candidate(s)."
            )
        selected.append(analysis.candidates[number - 1])

    plan = ClipEditPlan(
        source=analysis.source,
        ranges=tuple(
            ClipRange(candidate.start_seconds, candidate.end_seconds)
            for candidate in selected
        ),
        transcript=analysis.transcript,
        settings=_output_settings(args),
    )
    result = LocalClipRenderer().render(plan, args.output.expanduser())
    print(f"Rendered video: {result.path}")
    print(
        f"Duration: {result.duration_seconds:.2f}s; "
        f"resolution: {result.width}x{result.height}; "
        f"audio: {'yes' if result.has_audio else 'no'}"
    )


def _clip(args: argparse.Namespace) -> None:
    clip_range = ClipRange(args.start, args.end)
    duration = clip_range.end_seconds - clip_range.start_seconds
    if duration > MAX_MANUAL_CLIP_SECONDS:
        raise ValueError("Manual clip length must not exceed 90 seconds.")
    settings = _output_settings(args)
    transcriber = None
    if not args.no_captions:
        if args.whisper_model is None:
            raise ValueError(
                "Provide --whisper-model to transcribe captions, or use "
                "--no-captions."
            )
        transcriber = FasterWhisperTranscriber(args.whisper_model)
    result = LocalManualClipper().render(
        video_path=args.video.expanduser(),
        start_seconds=clip_range.start_seconds,
        end_seconds=clip_range.end_seconds,
        output_path=args.output.expanduser(),
        settings=settings,
        transcriber=transcriber,
        language=args.language,
    )
    print(f"Rendered video: {result.path}")
    print(
        f"Duration: {result.duration_seconds:.2f}s; "
        f"resolution: {result.width}x{result.height}; "
        f"audio: {'yes' if result.has_audio else 'no'}"
    )


def _output_settings(args: argparse.Namespace) -> ClipOutputSettings:
    return ClipOutputSettings(
        width=args.width,
        height=args.height,
        fps=args.fps,
        fit_mode=args.fit_mode,
        caption_style=args.caption_style,
        quality_preset=args.quality_preset,
        burn_captions=not args.no_captions,
        caption_font_size=args.caption_font_size,
        sharpness=args.sharpness,
        contrast=args.contrast,
        saturation=args.saturation,
        grain=args.grain,
        creator_credit=args.creator_credit,
    )


def _write_analysis(path: Path, analysis: ClipAnalysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "source": {
            **asdict(analysis.source),
            "path": str(analysis.source.path),
        },
        "transcript": [asdict(segment) for segment in analysis.transcript],
        "candidates": [asdict(candidate) for candidate in analysis.candidates],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_analysis(path: Path) -> ClipAnalysis:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Analysis file is not valid JSON: {path}") from error
    if not isinstance(document, dict):
        raise ValueError("Analysis file must contain a JSON object.")
    if document.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError("Unsupported analysis file version.")

    source_data = document.get("source")
    transcript_data = document.get("transcript")
    candidates_data = document.get("candidates")
    if (
        not isinstance(source_data, dict)
        or not isinstance(transcript_data, list)
        or not isinstance(candidates_data, list)
    ):
        raise ValueError("Analysis file is missing source, transcript, or candidates.")

    source_data = dict(source_data)
    source_data["path"] = Path(source_data["path"])
    source = VideoSource(**source_data)
    transcript = tuple(
        TranscriptSegment(
            **{
                **segment,
                "words": tuple(
                    TranscriptWord(**word)
                    for word in segment.get("words", [])
                ),
            }
        )
        for segment in transcript_data
    )
    candidates = tuple(
        MomentCandidate(**candidate) for candidate in candidates_data
    )
    return ClipAnalysis(source, transcript, candidates)


if __name__ == "__main__":
    main()
