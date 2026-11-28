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
    TranscriptWord,
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
            TranscriptSegment(
                1,
                5,
                "First moment.",
                (
                    TranscriptWord(1.1, 1.5, "First"),
                    TranscriptWord(1.6, 2.0, "moment."),
                ),
            ),
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
    client_options = []
    transcriber_options = []

    class FakeAnalyzer:
        def analyze(self, video_path, **options):
            captured["video_path"] = video_path
            captured["options"] = options
            return analysis

    monkeypatch.setattr(
        cli,
        "FasterWhisperTranscriber",
        lambda model_path, **kwargs: transcriber_options.append(
            (model_path, kwargs)
        )
        or object(),
    )
    monkeypatch.setattr(
        cli,
        "OllamaClient",
        lambda **kwargs: client_options.append(kwargs) or object(),
    )
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
    assert transcriber_options == [
        (tmp_path / "model", {"device": "cpu"})
    ]
    assert client_options == [
        {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen2.5:3b",
            "cpu_only": True,
        }
    ]


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
            "--fit-mode",
            "blurred",
            "--caption-style",
            "gaming",
            "--sharpness",
            "0.75",
            "--contrast",
            "1.1",
            "--saturation",
            "1.2",
            "--grain",
            "5",
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
    assert plan.settings.fit_mode == "blurred"
    assert plan.settings.caption_style == "gaming"
    assert plan.settings.burn_captions is False
    assert plan.settings.sharpness == 0.75
    assert plan.settings.contrast == 1.1
    assert plan.settings.saturation == 1.2
    assert plan.settings.grain == 5
    assert plan.transcript[0].words[0].text == "First"
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


def test_clip_command_transcribes_and_renders_exact_short_range(
    tmp_path, monkeypatch, capsys
):
    video = tmp_path / "source.mp4"
    create_analysis(video)
    output = tmp_path / "manual.mp4"
    captured = {}

    transcriber = object()

    class FakeManualClipper:
        def render(
            self,
            video_path,
            start_seconds,
            end_seconds,
            output_path,
            settings,
            **options,
        ):
            captured.update(options)
            captured.update(
                video_path=video_path,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                output_path=output_path,
                settings=settings,
            )
            return RenderedClip(output, 5.5, 1080, 1920, True, 1)

    monkeypatch.setattr(cli, "FasterWhisperTranscriber", lambda _path: transcriber)
    monkeypatch.setattr(cli, "LocalManualClipper", FakeManualClipper)

    cli.main(
        [
            "clip",
            str(video),
            "--start",
            "00:10.5",
            "--end",
            "00:16",
            "--whisper-model",
            str(tmp_path / "model"),
            "--language",
            "en",
            "--output",
            str(output),
            "--caption-style",
            "gaming",
        ]
    )

    assert captured["video_path"] == video
    assert captured["start_seconds"] == 10.5
    assert captured["end_seconds"] == 16.0
    assert captured["transcriber"] is transcriber
    assert captured["language"] == "en"
    assert captured["output_path"] == output
    assert captured["settings"].burn_captions is True
    assert captured["settings"].caption_style == "gaming"
    assert "Rendered video:" in capsys.readouterr().out


def test_clip_command_rejects_ranges_over_90_seconds_before_processing(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "clip",
                "source.mp4",
                "--start",
                "0",
                "--end",
                "90.01",
                "--output",
                "manual.mp4",
            ]
        )

    assert error.value.code == 2
    assert "must not exceed 90 seconds" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.5", 12.5),
        ("01:30", 90.0),
        ("00:01:30.5", 90.5),
    ],
)
def test_parse_time_accepts_seconds_and_timecodes(value, expected):
    assert cli._parse_time(value) == expected


@pytest.mark.parametrize("value", ["-1", "00:60", "1:2:3:4", "nan"])
def test_parse_time_rejects_invalid_values(value):
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli._parse_time(value)
