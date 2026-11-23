"""Tests for rendering user-selected local video ranges."""

import hashlib
from pathlib import Path

import pytest

from app.repurpose.editing import ClipEditPlan, ClipOutputSettings, ClipRange
from app.repurpose.models import TranscriptSegment, VideoSource
from app.repurpose.renderer import LocalClipRenderer
from app.utils.ffmpeg import VideoInfo


class FakeCatalog:
    def __init__(self, source):
        self.source = source

    def inspect(self, path):
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
    assert "ass='" in first_command[first_command.index("-vf") + 1]
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
