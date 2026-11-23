"""Tests for media processor."""

import tempfile
from pathlib import Path
import pytest
from PIL import Image
from app.media import MediaProcessor


@pytest.fixture
def processor():
    """Create a media processor instance."""
    return MediaProcessor(
        caption_font_size=48,
        caption_padding=20,
        caption_bg_color=(0, 0, 0, 200),
        caption_text_color=(255, 255, 255, 255),
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe"
    )


@pytest.fixture
def temp_image():
    """Create a temporary test image."""
    img = Image.new('RGB', (800, 600), color='blue')

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        test_file = Path(f.name)
        img.save(test_file)

    yield test_file

    if test_file.exists():
        test_file.unlink()


def test_processor_initialization(processor):
    """Test processor initialization."""
    assert processor.caption_font_size == 48
    assert processor.caption_padding == 20
    assert processor.caption_bg_color == (0, 0, 0, 200)
    assert processor.caption_text_color == (255, 255, 255, 255)


def test_process_image(processor, temp_image):
    """Test image processing with caption."""
    output_path = temp_image.parent / "output_test.jpg"

    try:
        success = processor.process_image(
            temp_image,
            output_path,
            "Test Caption"
        )

        assert success
        assert output_path.exists()

        # Verify output is valid image
        with Image.open(output_path) as img:
            assert img.size == (800, 600)

    finally:
        if output_path.exists():
            try:
                output_path.unlink()
            except PermissionError:
                pass  # Windows file lock, will be cleaned up by temp dir


def test_process_image_invalid_input(processor):
    """Test processing non-existent image."""
    output_path = Path("/tmp/output.jpg")

    success = processor.process_image(
        Path("/nonexistent/input.jpg"),
        output_path,
        "Test Caption"
    )

    assert not success


def test_wrap_text(processor):
    """Test text wrapping."""
    from PIL import ImageDraw, ImageFont

    img = Image.new('RGB', (400, 300))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default()
    except:
        font = None

    text = "This is a very long caption that should be wrapped across multiple lines"
    wrapped = processor._wrap_text(text, 200, font or ImageFont.load_default(), draw)

    assert isinstance(wrapped, list)
    assert len(wrapped) > 1  # Should be split into multiple lines


def test_check_ffmpeg_available(processor):
    """Test FFmpeg availability check."""
    # This will depend on the system
    result = processor.check_ffmpeg_available()
    assert isinstance(result, bool)


def test_process_video_without_ffmpeg(processor):
    """Test video processing fails gracefully without FFmpeg."""
    # Create a fake video path
    video_path = Path("/tmp/test.mp4")
    output_path = Path("/tmp/output.mp4")

    # If FFmpeg not available, should return False
    # If available, should fail on invalid input file
    success = processor.process_video(video_path, output_path, "Test Caption")

    # Either way, should not raise exception
    assert isinstance(success, bool)
