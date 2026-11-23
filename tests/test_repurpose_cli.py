"""Tests for the local repurposing terminal commands."""

import hashlib
import json

import pytest

from app.repurpose import cli
from app.repurpose.editing import ClipEditPlan, RenderedClip
from app.repurpose.models import (
    ClipAnalysis,
    MomentCandidate,
    TranscriptSegment,
    VideoSource,
)


def create_analysis(video_path):
    video_path.write_bytes(b"original video")
    source = VideoSource(
        path=video_path.resolve(),
        sha256=hashlib.sha256(b"original video").hexdigest(),
        file_size_bytes=len(b"original video"),
        duration_seconds=60,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        aspect_ratio="16:9",
    )
    return ClipAnalysis(
        source=source,
        transcript=(
            TranscriptSegment(1, 5, "First moment."),
            TranscriptSegment(10, 15, "Second moment."),
        ),
        candidates=(
            MomentCandidate(0, 10, "First moment.", 0.9, "Strong hook."),
            MomentCandidate(10, 20, "Second moment.", 0.8, "Clear point."),
        ),
    )


def test_analyze_command_saves_versioned_local_analysis(tmp_path, monkeypatch, capsys):
    video = tmp_path / "source.mp4"
    analysis = create_analysis(video)
    output = tmp_path / "analysis.json"
    captured = {}

    class FakeAnalyzer:
        def analyze(self, video_path, **options):
            captured["video_path"] = video_path
            captured["options"] = options
            return analysis

    monkeypatch.setattr(
        cli, "FasterWhisperTranscriber", lambda model_path: object()
    )
    monkeypatch.setattr(cli, "OllamaClient", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "OllamaMomentRanker", lambda _client: object())
    monkeypatch.setattr(
        cli, "LocalClipAnalyzer", lambda **_kwargs: FakeAnalyzer()
    )

    cli.main(
        [
            "analyze",
            str(video),
            "--whisper-model",
            str(tmp_path / "model"),
            "--analysis-output",
            str(output),
            "--min-duration",
            "8",
            "--max-duration",
            "20",
            "--language",
            "en",
        ]
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["source"]["path"] == str(video.resolve())
    assert len(document["transcript"]) == 2
    assert len(document["candidates"]) == 2
    assert captured["video_path"] == video.resolve()
    assert captured["options"]["min_duration"] == 8
    assert captured["options"]["max_duration"] == 20
    assert captured["options"]["language"] == "en"
    assert "1. 0.00-10.00s" in capsys.readouterr().out


def test_render_command_passes_selected_moments_as_one_compilation(
    tmp_path, monkeypatch, capsys
):
    video = tmp_path / "source.mp4"
    analysis = create_analysis(video)
    analysis_path = tmp_path / "analysis.json"
    cli._write_analysis(analysis_path, analysis)
    output = tmp_path / "compiled.mp4"
    captured = {}

    class FakeRenderer:
        def render(self, plan, output_path):
            captured["plan"] = plan
            captured["output_path"] = output_path
            return RenderedClip(output, 20, 1080, 1920, True, 2)

    monkeypatch.setattr(cli, "LocalClipRenderer", FakeRenderer)

    cli.main(
        [
            "render",
            str(analysis_path),
            "--candidate",
            "2",
            "--candidate",
            "1",
            "--output",
            str(output),
            "--width",
            "720",
            "--height",
            "1280",
            "--no-captions",
        ]
    )

    plan = captured["plan"]
    assert isinstance(plan, ClipEditPlan)
    assert [
        (item.start_seconds, item.end_seconds) for item in plan.ranges
    ] == [(10, 20), (0, 10)]
    assert plan.settings.width == 720
    assert plan.settings.height == 1280
    assert plan.settings.burn_captions is False
    assert captured["output_path"] == output
    assert "Rendered video:" in capsys.readouterr().out


def test_render_command_rejects_candidate_index_out_of_range(
    tmp_path, monkeypatch
):
    video = tmp_path / "source.mp4"
    analysis = create_analysis(video)
    analysis_path = tmp_path / "analysis.json"
    cli._write_analysis(analysis_path, analysis)

    class UnexpectedRenderer:
        def __init__(self):
            raise AssertionError("Invalid candidate must fail before rendering.")

    monkeypatch.setattr(cli, "LocalClipRenderer", UnexpectedRenderer)

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "render",
                str(analysis_path),
                "--candidate",
                "3",
                "--output",
                str(tmp_path / "compiled.mp4"),
            ]
        )

    assert error.value.code == 2
