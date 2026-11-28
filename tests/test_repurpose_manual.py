import hashlib
from pathlib import Path
from threading import Event

import pytest

from app.repurpose.editing import ClipOutputSettings, RenderedClip
from app.repurpose.models import TranscriptSegment, TranscriptWord, VideoSource
from app.repurpose.service import LocalManualClipper


def create_source(path: Path, duration: float = 120) -> VideoSource:
    content = b"source video"
    path.write_bytes(content)
    return VideoSource(
        path=path.resolve(),
        sha256=hashlib.sha256(content).hexdigest(),
        file_size_bytes=len(content),
        duration_seconds=duration,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        aspect_ratio="16:9",
    )


def test_manual_clipper_transcribes_only_selected_audio_and_offsets_captions(
    tmp_path,
):
    source = create_source(tmp_path / "source.mp4")
    output = tmp_path / "manual.mp4"
    cancel_event = Event()
    captured = {}
    progress = []

    class FakeCatalog:
        def inspect(self, path, cancel_event=None):
            captured["inspected_path"] = path
            captured["inspect_cancel_event"] = cancel_event
            return source

    class FakeFFmpeg:
        def execute_cancellable(
            self,
            args,
            *,
            expected_duration_seconds,
            cancel_event,
            progress_callback,
            timeout,
        ):
            captured["ffmpeg_args"] = args
            captured["expected_duration"] = expected_duration_seconds
            captured["ffmpeg_cancel_event"] = cancel_event
            captured["ffmpeg_timeout"] = timeout
            audio_path = Path(args[-1])
            audio_path.write_bytes(b"selected audio")
            captured["temporary_audio_path"] = audio_path
            progress_callback(0.5)
            return 0, "", ""

    class FakeTranscriber:
        def transcribe(self, audio_path, language=None, cancel_event=None):
            assert audio_path.is_file()
            captured["transcribed_path"] = audio_path
            captured["language"] = language
            captured["transcriber_cancel_event"] = cancel_event
            return (
                TranscriptSegment(
                    1,
                    3,
                    "Hello world.",
                    (
                        TranscriptWord(1.1, 1.5, "Hello"),
                        TranscriptWord(1.6, 2.0, "world."),
                    ),
                ),
            )

    class FakeRenderer:
        def render(self, plan, output_path, **options):
            captured["plan"] = plan
            captured["output_path"] = output_path
            captured["render_options"] = options
            options["progress_callback"](0.5)
            return RenderedClip(output, 5.5, 1080, 1920, True, 1)

    ffmpeg = FakeFFmpeg()
    clipper = LocalManualClipper(
        ffmpeg=ffmpeg,
        catalog=FakeCatalog(),
        renderer=FakeRenderer(),
    )

    result = clipper.render(
        source.path,
        10.5,
        16,
        output,
        ClipOutputSettings(),
        transcriber=FakeTranscriber(),
        language="en",
        cancel_event=cancel_event,
        progress_callback=progress.append,
    )

    plan = captured["plan"]
    assert [(item.start_seconds, item.end_seconds) for item in plan.ranges] == [
        (10.5, 16)
    ]
    assert plan.transcript[0].start_seconds == 11.5
    assert plan.transcript[0].end_seconds == 13.5
    assert plan.transcript[0].words[0].start_seconds == 11.6
    assert captured["expected_duration"] == 5.5
    assert captured["language"] == "en"
    assert captured["ffmpeg_cancel_event"] is cancel_event
    assert captured["transcriber_cancel_event"] is cancel_event
    assert not captured["temporary_audio_path"].exists()
    assert progress == [0.05, 0.55]
    assert result.path == output


def test_manual_clipper_rejects_ranges_over_90_seconds_before_inspecting(
    tmp_path,
):
    source = create_source(tmp_path / "source.mp4")

    class UnexpectedCatalog:
        def inspect(self, _path):
            raise AssertionError("Overlong ranges must fail before source inspection.")

    clipper = LocalManualClipper(catalog=UnexpectedCatalog())

    with pytest.raises(ValueError, match="must not exceed 90 seconds"):
        clipper.render(
            source.path,
            0,
            90.01,
            tmp_path / "manual.mp4",
            ClipOutputSettings(),
        )


def test_manual_clipper_without_captions_skips_transcription(tmp_path):
    source = create_source(tmp_path / "source.mp4")
    output = tmp_path / "manual.mp4"
    captured = {}

    class FakeCatalog:
        def inspect(self, path):
            captured["source_path"] = path
            return source

    class FakeRenderer:
        def render(self, plan, output_path, **_options):
            captured["plan"] = plan
            return RenderedClip(output, 4, 1080, 1920, True, 1)

    clipper = LocalManualClipper(
        catalog=FakeCatalog(),
        renderer=FakeRenderer(),
    )
    result = clipper.render(
        source.path,
        1,
        5,
        output,
        ClipOutputSettings(burn_captions=False),
    )

    assert captured["plan"].transcript == ()
    assert result.path == output
