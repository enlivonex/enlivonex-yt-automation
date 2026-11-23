"""Tests for video composition."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from app.composition.composer import ShortsComposer, CompositionConfig
from app.composition.duration import DurationManager
from app.voiceover.models import VoiceSegment
from app.captions.timed import CaptionWord, CaptionSegment, CaptionStyle, TimedCaptions


@pytest.fixture
def mock_ffmpeg():
    """Create mock FFmpeg wrapper."""
    mock = Mock()
    mock.is_available.return_value = True
    mock.probe.return_value = Mock(
        duration=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264",
        bitrate=5000000,
        aspect_ratio="16:9",
        orientation="landscape"
    )
    mock.probe_duration.return_value = 2.5
    mock.has_audio_stream.return_value = True
    mock.execute.return_value = (0, "", "")
    return mock


@pytest.fixture
def sample_voiceover(tmp_path):
    """Create sample voiceover segments."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    segments = []
    for i in range(3):
        audio_file = audio_dir / f"segment_{i}.mp3"
        audio_file.write_bytes(b"fake audio")

        segments.append(VoiceSegment(
            text=f"Segment {i}",
            audio_path=audio_file,
            duration=3.0,
            start_time=i * 3.0,
            end_time=(i + 1) * 3.0
        ))

    return segments


@pytest.fixture
def sample_captions():
    """Create sample captions."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 0.0, 1.0)],
            text="Test caption",
            start_time=0.0,
            end_time=1.0
        )
    ]

    return TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=1.0
    )


def test_composition_config_defaults():
    """Test default composition config."""
    config = CompositionConfig()

    assert config.width == 1080
    assert config.height == 1920
    assert config.fps == 30
    assert config.video_codec == "libx264"


def test_composer_initialization():
    """Test composer initialization."""
    composer = ShortsComposer()

    assert composer.ffmpeg is not None


def test_composer_requires_ffmpeg(tmp_path, sample_voiceover, sample_captions):
    """Test composer fails without FFmpeg."""
    mock_ffmpeg = Mock()
    mock_ffmpeg.is_available.return_value = False

    composer = ShortsComposer(ffmpeg=mock_ffmpeg)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")

    output = tmp_path / "output.mp4"

    with pytest.raises(RuntimeError, match="FFmpeg not available"):
        composer.compose(source, sample_voiceover, sample_captions, output)


def test_merge_single_voiceover(mock_ffmpeg, sample_voiceover, tmp_path):
    """Test merging single voiceover segment."""
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Single segment
    merged = composer._merge_voiceover([sample_voiceover[0]], temp_dir)

    assert merged.exists()
    assert merged.name == "merged_audio.mp3"


def test_merge_multiple_voiceovers(mock_ffmpeg, sample_voiceover, tmp_path):
    """Test merging multiple voiceover segments."""
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Mock should create the merged file
    def mock_execute(args, timeout=None):
        # Extract output path from args
        if str(temp_dir / "merged_audio.mp3") in [str(arg) for arg in args]:
            (temp_dir / "merged_audio.mp3").write_bytes(b"merged audio")
        return (0, "", "")

    mock_ffmpeg.execute.side_effect = mock_execute

    merged = composer._merge_voiceover(sample_voiceover, temp_dir)

    assert merged.exists()
    mock_ffmpeg.execute.assert_called_once()


def test_merge_no_segments(mock_ffmpeg, tmp_path):
    """Test merge fails with no segments."""
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    with pytest.raises(ValueError, match="No voiceover segments"):
        composer._merge_voiceover([], temp_dir)


def test_cleanup_temp(mock_ffmpeg, tmp_path):
    """Test temporary directory cleanup."""
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create some files
    (temp_dir / "file1.txt").write_text("test")
    (temp_dir / "file2.txt").write_text("test")

    composer._cleanup_temp(temp_dir)

    assert not temp_dir.exists()


def test_composition_config_custom():
    """Test custom composition config."""
    config = CompositionConfig(
        width=720,
        height=1280,
        fps=60,
        quality_preset="fast"
    )

    assert config.width == 720
    assert config.height == 1280
    assert config.fps == 60
    assert config.quality_preset == "fast"


@pytest.mark.parametrize(
    ("width", "height"),
    [(1080, 1920), (1920, 1080), (1080, 1080)],
)
def test_orientation_filters_preserve_selected_output_dimensions(width, height):
    filters = ShortsComposer._orientation_filters(
        CompositionConfig(width=width, height=height)
    )

    assert filters == [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
    ]


def test_trim_audio_uses_requested_duration(mock_ffmpeg, tmp_path):
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    source = tmp_path / "long_audio.mp3"
    source.write_bytes(b"audio")

    result = composer._trim_audio(source, 25.0, tmp_path)

    args = mock_ffmpeg.execute.call_args.args[0]
    assert result.name == "trimmed_audio.mp3"
    assert args[args.index("-t") + 1] == "25.000000"
    assert args[args.index("-i") + 1] == str(source)


def test_compose_caps_audio_and_caption_file_to_selected_duration(
    mock_ffmpeg, sample_voiceover, sample_captions, tmp_path, monkeypatch
):
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    captured = {}

    def capture_compose(**kwargs):
        captured.update(kwargs)
        captured["caption_text"] = kwargs["caption_file"].read_text(
            encoding="utf-8"
        )

    monkeypatch.setattr(composer, "_compose_final", capture_compose)

    composer.compose(
        source,
        sample_voiceover[:1],
        sample_captions,
        output,
        CompositionConfig(target_duration=0.5),
    )

    assert captured["audio_path"].name == "trimmed_audio.mp3"
    assert captured["config"].target_duration == 0.5
    caption_text = captured["caption_text"]
    assert "0:00:00.50" in caption_text
    assert "0:00:01.00" not in caption_text


def test_compose_preserves_full_audio_and_captions_without_a_target(
    mock_ffmpeg, sample_voiceover, sample_captions, tmp_path, monkeypatch
):
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    captured = {}

    def capture_compose(**kwargs):
        captured.update(kwargs)
        captured["caption_text"] = kwargs["caption_file"].read_text(
            encoding="utf-8"
        )

    monkeypatch.setattr(composer, "_compose_final", capture_compose)

    composer.compose(
        source,
        sample_voiceover[:1],
        sample_captions,
        output,
        CompositionConfig(),
    )

    assert captured["audio_path"].name == "merged_audio.mp3"
    assert captured["config"].target_duration is None
    assert "0:00:01.00" in captured["caption_text"]


def test_caption_trim_drops_content_past_duration():
    captions = TimedCaptions(
        segments=[
            CaptionSegment(
                words=[
                    CaptionWord("first", 0.0, 1.0),
                    CaptionWord("second", 1.0, 2.0),
                ],
                text="first second",
                start_time=0.0,
                end_time=2.0,
            ),
            CaptionSegment(
                words=[CaptionWord("later", 2.0, 3.0)],
                text="later",
                start_time=2.0,
                end_time=3.0,
            ),
        ],
        style=CaptionStyle(),
        total_duration=3.0,
    )

    trimmed = captions.trim(1.5)

    assert [segment.text for segment in trimmed.segments] == ["first second"]
    assert trimmed.segments[0].end_time == 1.5
    assert trimmed.total_duration == 1.5


@pytest.mark.parametrize(
    ("source_duration", "target_duration", "should_loop"),
    [(180.0, 35.0, False), (8.0, 35.0, True), (35.1, 35.0, False)],
)
def test_duration_manager_plans_trim_loop_or_direct_use(
    source_duration, target_duration, should_loop
):
    plan = DurationManager().plan(source_duration, target_duration)

    assert plan.loop_video is should_loop
    assert plan.target_duration == target_duration


def test_duration_manager_rejects_rendered_duration_mismatch():
    with pytest.raises(RuntimeError, match="does not match narration"):
        DurationManager(tolerance_seconds=0.25).verify(30.0, 35.0)


def test_composition_maps_audio_once_and_bounds_output_to_audio(
    mock_ffmpeg, sample_captions, tmp_path
):
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    captions = tmp_path / "captions.ass"
    captions.write_text("captions")
    output = tmp_path / "output.mp4"

    composer._compose_final(source, audio, captions, output, CompositionConfig())

    args = mock_ffmpeg.execute.call_args.args[0]
    assert args.count("-i") == 2
    assert "-stream_loop" not in args
    assert args[args.index("-map") + 1] == "0:v:0"
    audio_map = args.index("-map", args.index("-map") + 1)
    assert args[audio_map + 1] == "1:a:0"
    assert args[args.index("-t") + 1] == "2.500000"
    assert any("captions.ass" in arg for arg in args)


def test_short_video_loop_option_applies_only_to_video_input(
    mock_ffmpeg, sample_captions, tmp_path
):
    mock_ffmpeg.probe.return_value.duration = 0.5
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    source = tmp_path / "short.mp4"
    audio = tmp_path / "narration.mp3"
    caption = tmp_path / "captions.ass"
    source.write_bytes(b"video")
    audio.write_bytes(b"audio")
    caption.write_text("captions")

    composer._compose_final(
        source, audio, caption, tmp_path / "output.mp4", CompositionConfig()
    )

    args = mock_ffmpeg.execute.call_args.args[0]
    loop_index = args.index("-stream_loop")
    input_indices = [index for index, arg in enumerate(args) if arg == "-i"]
    assert args[loop_index + 1] == "-1"
    assert loop_index < input_indices[0] < input_indices[1]


def test_multiple_source_clips_concatenate_and_loop_video_only_when_needed(
    mock_ffmpeg, sample_captions, tmp_path
):
    from types import SimpleNamespace

    mock_ffmpeg.probe.side_effect = [
        SimpleNamespace(duration=1.0, orientation="landscape"),
        SimpleNamespace(duration=1.0, orientation="square"),
    ]
    mock_ffmpeg.probe_duration.return_value = 3.5
    composer = ShortsComposer(ffmpeg=mock_ffmpeg)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    audio = tmp_path / "narration.mp3"
    caption = tmp_path / "captions.ass"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    audio.write_bytes(b"audio")
    caption.write_text("captions")

    composer._compose_final(
        [first, second], audio, caption, tmp_path / "output.mp4",
        CompositionConfig(),
    )

    args = mock_ffmpeg.execute.call_args.args[0]
    input_indices = [index for index, arg in enumerate(args) if arg == "-i"]
    loop_index = args.index("-stream_loop")
    assert args.count("-i") == 3
    assert loop_index < input_indices[1] < input_indices[2]
    assert args[loop_index + 1] == "-1"
    assert args[args.index("-map") + 1] == "[captioned]"
    audio_map = args.index("-map", args.index("-map") + 1)
    assert args[audio_map + 1] == "2:a:0"
    filter_graph = args[args.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=0" in filter_graph
    assert "captions.ass" in filter_graph
    assert args[args.index("-t") + 1] == "3.500000"


@pytest.mark.parametrize("source_duration", [0.8, 4.0])
def test_real_composition_syncs_video_audio_and_burns_captions(
    tmp_path, sample_captions, source_duration
):
    from app.utils.ffmpeg import FFmpegWrapper

    ffmpeg = FFmpegWrapper()
    if not ffmpeg.is_available():
        pytest.skip("FFmpeg/FFprobe are not installed")

    source = tmp_path / "source.mp4"
    generated = ffmpeg.execute([
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=24:d={source_duration}",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(source)
    ])
    assert generated[0] == 0, generated[2]

    audio = tmp_path / "narration.mp3"
    generated = ffmpeg.execute([
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "2", "-q:a", "9", "-acodec", "libmp3lame", "-y", str(audio)
    ])
    assert generated[0] == 0, generated[2]

    caption = TimedCaptions(
        segments=[
            CaptionSegment(
                words=[CaptionWord("Narration", 0.0, 1.5)],
                text="Narration",
                start_time=0.0,
                end_time=1.5,
            )
        ],
        style=CaptionStyle(),
        total_duration=2.0,
    )
    segment = VoiceSegment(
        text="Narration", audio_path=audio, duration=2.0,
        start_time=0.0, end_time=2.0
    )
    output = tmp_path / "output.mp4"

    ShortsComposer(ffmpeg=ffmpeg).compose(source, [segment], caption, output)

    assert output.exists()
    assert ffmpeg.has_audio_stream(output)
    ffmpeg_duration = ffmpeg.probe_duration(output)
    audio_duration = ffmpeg.probe_duration(audio)
    assert abs(ffmpeg_duration - audio_duration) <= 0.5

    decoded = ffmpeg.execute(["-v", "error", "-i", str(output), "-f", "null", "-"])
    assert decoded[0] == 0, decoded[2]


def test_real_composition_joins_distinct_clips_with_spoken_audio_track_and_captions(
    tmp_path,
):
    from app.utils.ffmpeg import FFmpegWrapper

    ffmpeg = FFmpegWrapper()
    if not ffmpeg.is_available():
        pytest.skip("FFmpeg/FFprobe are not installed")

    sources = []
    for index, (color, size) in enumerate(
        [("red", "320x240"), ("blue", "240x240")]
    ):
        source = tmp_path / f"source_{index}.mp4"
        generated = ffmpeg.execute([
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d=0.7",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-y", str(source),
        ])
        assert generated[0] == 0, generated[2]
        sources.append(source)

    audio = tmp_path / "narration.mp3"
    generated = ffmpeg.execute([
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:a", "libmp3lame", "-y", str(audio),
    ])
    assert generated[0] == 0, generated[2]

    captions = TimedCaptions(
        segments=[
            CaptionSegment(
                words=[CaptionWord("Narration", 0.0, 1.5)],
                text="Narration",
                start_time=0.0,
                end_time=1.5,
            )
        ],
        style=CaptionStyle(),
        total_duration=2.0,
    )
    voice = VoiceSegment(
        text="Narration", audio_path=audio, duration=2.0,
        start_time=0.0, end_time=2.0,
    )
    output = tmp_path / "multi_source_output.mp4"

    ShortsComposer(ffmpeg=ffmpeg).compose(sources, [voice], captions, output)

    assert output.exists()
    assert ffmpeg.has_audio_stream(output)
    assert abs(ffmpeg.probe_duration(output) - ffmpeg.probe_duration(audio)) <= 0.5
    decoded = ffmpeg.execute(["-v", "error", "-i", str(output), "-f", "null", "-"])
    assert decoded[0] == 0, decoded[2]
