"""Tests for rendering user-selected local video ranges."""

import hashlib
from pathlib import Path
from concurrent.futures import CancelledError
from threading import Event

import pytest

from app.repurpose.editing import ClipEditPlan, ClipOutputSettings, ClipRange
from app.repurpose.models import TranscriptSegment, VideoSource
from app.repurpose.renderer import LocalClipRenderer
from app.utils.ffmpeg import VideoInfo


class FakeCatalog:
    def __init__(self, source):
        self.source = source

    def inspect(self, path, cancel_event=None):
        assert Path(path) == self.source.path
        return self.source


class FakeFFmpeg:
    def __init__(self, output_duration=20, output_width=1080, output_height=1920):
        self.output_duration = output_duration
        self.output_width = output_width
        self.output_height = output_height
        self.commands = []
        self.available = True

    def is_available(self):
        return self.available

    def has_audio_stream(self, path):
        return True

    def execute(self, args, timeout):
        self.commands.append((args, timeout))
        Path(args[-1]).write_bytes(b"rendered")
        return 0, "", ""

    def execute_cancellable(
        self,
        args,
        *,
        timeout,
        expected_duration_seconds,
        cancel_event=None,
        progress_callback=None,
    ):
        self.commands.append((args, timeout))
        Path(args[-1]).write_bytes(b"rendered")
        if progress_callback is not None:
            progress_callback(0.5)
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Cancellation requested by the user.")
        if progress_callback is not None:
            progress_callback(1.0)
        return 0, "", ""

    def probe(self, path):
        if Path(path).name == "compiled.mp4":
            duration = self.output_duration
            width = self.output_width
            height = self.output_height
        else:
            duration = 120
            width = 1920
            height = 1080
        return VideoInfo(duration, width, height, 30, "h264", None, "16:9")


def create_source(path):
    contents = b"original video bytes"
    path.write_bytes(contents)
    return VideoSource(
        path=path.resolve(),
        sha256=hashlib.sha256(contents).hexdigest(),
        file_size_bytes=len(contents),
        duration_seconds=120,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        aspect_ratio="16:9",
    )


def test_edit_plan_rejects_empty_or_out_of_bounds_ranges(tmp_path):
    source = create_source(tmp_path / "source.mp4")

    with pytest.raises(ValueError, match="at least one clip range"):
        ClipEditPlan(source, ())
    with pytest.raises(ValueError, match="exceeds source"):
        ClipEditPlan(source, (ClipRange(110, 121),))


@pytest.mark.parametrize(
    "settings",
    [
        {"width": 1079},
        {"height": 1919},
        {"fps": 0},
        {"fit_mode": "stretch"},
        {"quality_preset": "unknown"},
        {"sharpness": -0.1},
        {"sharpness": 1.6},
        {"contrast": 1.6},
        {"contrast": float("nan")},
        {"saturation": -0.1},
        {"saturation": 2.1},
        {"grain": -1},
        {"grain": 21},
        {"grain": 1.5},
    ],
)
def test_output_settings_reject_invalid_values(settings):
    with pytest.raises(ValueError):
        ClipOutputSettings(**settings)


def test_renderer_compiles_ranges_captions_and_preserves_source(tmp_path):
    source_path = tmp_path / "source.mp4"
    source = create_source(source_path)
    original_bytes = source_path.read_bytes()
    ffmpeg = FakeFFmpeg(output_duration=20)
    plan = ClipEditPlan(
        source=source,
        ranges=(ClipRange(0, 10), ClipRange(40, 50)),
        transcript=(
            TranscriptSegment(1, 4, "First moment."),
            TranscriptSegment(42, 48, "Second moment."),
        ),
    )
    output = tmp_path / "renders" / "compilation.mp4"

    result = LocalClipRenderer(ffmpeg, FakeCatalog(source)).render(plan, output)

    assert result.path == output.resolve()
    assert result.duration_seconds == 20
    assert result.width == 1080
    assert result.height == 1920
    assert result.has_audio is True
    assert result.source_range_count == 2
    assert output.read_bytes() == b"rendered"
    assert source_path.read_bytes() == original_bytes
    assert len(ffmpeg.commands) == 3
    first_command, second_command, concat_command = [
        command for command, _timeout in ffmpeg.commands
    ]
    assert first_command[first_command.index("-ss") + 1] == "0.000000"
    assert second_command[second_command.index("-ss") + 1] == "40.000000"
    filters = first_command[first_command.index("-vf") + 1]
    assert "ass='" in filters
    assert "unsharp=" not in filters
    assert "eq=" not in filters
    assert "noise=" not in filters
    assert concat_command[concat_command.index("-c") + 1] == "copy"
    assert sorted(path.name for path in output.parent.iterdir()) == [
        "compilation.mp4"
    ]


def test_renderer_rejects_source_overwrite_and_existing_output(tmp_path):
    source = create_source(tmp_path / "source.mp4")
    renderer = LocalClipRenderer(FakeFFmpeg(), FakeCatalog(source))
    plan = ClipEditPlan(source, (ClipRange(1, 11),))

    with pytest.raises(ValueError, match="must not overwrite"):
        renderer.render(plan, source.path)

    existing_output = tmp_path / "existing.mp4"
    existing_output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        renderer.render(plan, existing_output)
    assert existing_output.read_bytes() == b"keep"


@pytest.mark.parametrize(
    ("duration", "width", "message"),
    [
        (18, 1080, "duration does not match"),
        (20, 720, "dimensions do not match"),
    ],
)
def test_renderer_does_not_publish_invalid_output(
    tmp_path, duration, width, message
):
    source = create_source(tmp_path / "source.mp4")
    output = tmp_path / "invalid.mp4"
    renderer = LocalClipRenderer(
        FakeFFmpeg(output_duration=duration, output_width=width),
        FakeCatalog(source),
    )

    with pytest.raises(RuntimeError, match=message):
        renderer.render(
            ClipEditPlan(source, (ClipRange(0, 20),)),
            output,
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == [source.path]


def test_renderer_applies_requested_creative_color_filters(tmp_path):
    source = create_source(tmp_path / "source.mp4")
    ffmpeg = FakeFFmpeg(output_duration=10)
    plan = ClipEditPlan(
        source,
        (ClipRange(0, 10),),
        settings=ClipOutputSettings(
            width=1080,
            height=1920,
            burn_captions=False,
            sharpness=0.8,
            contrast=1.1,
            saturation=1.2,
            grain=6,
        ),
    )

    LocalClipRenderer(ffmpeg, FakeCatalog(source)).render(
        plan, tmp_path / "graded.mp4"
    )

    filter_graph = ffmpeg.commands[0][0][
        ffmpeg.commands[0][0].index("-vf") + 1
    ]
    assert "luma_amount=0.8" in filter_graph
    assert "eq=contrast=1.1:saturation=1.2" in filter_graph
    assert "noise=alls=6:allf=t+u" in filter_graph


def test_renderer_reports_progress_and_stages(tmp_path):
    source = create_source(tmp_path / "source.mp4")
    ffmpeg = FakeFFmpeg(output_duration=20)
    plan = ClipEditPlan(
        source, (ClipRange(0, 10), ClipRange(40, 50))
    )
    progress = []
    stages = []

    result = LocalClipRenderer(ffmpeg, FakeCatalog(source)).render(
        plan,
        tmp_path / "progress.mp4",
        progress_callback=progress.append,
        stage_callback=stages.append,
    )

    assert result.path.is_file()
    assert progress[-1] == 1.0
    assert progress == sorted(progress)
    assert stages == [
        "Rendering moment 1 of 2...",
        "Rendering moment 2 of 2...",
        "Joining rendered moments...",
        "Validating rendered draft...",
        "Draft ready.",
    ]


def test_renderer_cancellation_cleans_temporary_files(tmp_path):
    source_path = tmp_path / "source.mp4"
    source = create_source(source_path)
    original_bytes = source_path.read_bytes()
    output = tmp_path / "cancelled.mp4"
    cancel_event = Event()
    renderer = LocalClipRenderer(FakeFFmpeg(), FakeCatalog(source))

    def cancel_after_progress(_fraction):
        cancel_event.set()

    with pytest.raises(CancelledError):
        renderer.render(
            ClipEditPlan(source, (ClipRange(0, 10),)),
            output,
            cancel_event=cancel_event,
            progress_callback=cancel_after_progress,
        )

    assert source_path.read_bytes() == original_bytes
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [source_path]
