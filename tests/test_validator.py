"""Tests for media validator."""

import tempfile
from pathlib import Path
import pytest
from app.media import MediaValidator


@pytest.fixture
def validator():
    """Create a validator instance."""
    return MediaValidator(
        supported_image_formats=['jpg', 'jpeg', 'png', 'webp'],
        supported_video_formats=['mp4', 'mov', 'webm', 'mkv'],
        max_file_size_mb=500
    )


def test_is_supported_format(validator):
    """Test format detection."""
    assert validator.is_supported_format("test.jpg")
    assert validator.is_supported_format("test.JPG")
    assert validator.is_supported_format("test.mp4")
    assert validator.is_supported_format("test.png")
    assert not validator.is_supported_format("test.txt")
    assert not validator.is_supported_format("test.pdf")


def test_is_image(validator):
    """Test image detection."""
    assert validator.is_image("test.jpg")
    assert validator.is_image("test.JPEG")
    assert validator.is_image("test.png")
    assert validator.is_image("test.webp")
    assert not validator.is_image("test.mp4")
    assert not validator.is_image("test.txt")


def test_is_video(validator):
    """Test video detection."""
    assert validator.is_video("test.mp4")
    assert validator.is_video("test.MP4")
    assert validator.is_video("test.mov")
    assert validator.is_video("test.webm")
    assert not validator.is_video("test.jpg")
    assert not validator.is_video("test.txt")


def test_get_media_type(validator):
    """Test media type detection."""
    assert validator.get_media_type("test.jpg") == "image"
    assert validator.get_media_type("test.mp4") == "video"
    assert validator.get_media_type("test.txt") is None


def test_validate_file_not_exists(validator):
    """Test validation of non-existent file."""
    is_valid, error = validator.validate_file(Path("/nonexistent/file.jpg"))
    assert not is_valid
    assert "does not exist" in error.lower()


def test_validate_file_unsupported_format(validator):
    """Test validation of unsupported format."""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
        test_file = Path(f.name)
        f.write(b"test content")

    try:
        is_valid, error = validator.validate_file(test_file)
        assert not is_valid
        assert "unsupported" in error.lower() or "format" in error.lower()
    finally:
        test_file.unlink()


def test_validate_file_too_large(validator):
    """Test validation of file exceeding size limit."""
    # Create validator with small limit
    small_validator = MediaValidator(
        supported_image_formats=['jpg'],
        supported_video_formats=[],
        max_file_size_mb=0.001  # 1KB limit
    )

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        test_file = Path(f.name)
        f.write(b"x" * 2048)  # 2KB file

    try:
        is_valid, error = small_validator.validate_file(test_file)
        assert not is_valid
        assert "too large" in error.lower()
    finally:
        test_file.unlink()


def test_validate_valid_file(validator):
    """Test validation of valid file."""
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        test_file = Path(f.name)
        f.write(b"fake image content")

    try:
        is_valid, error = validator.validate_file(test_file)
        assert is_valid
        assert error is None
    finally:
        test_file.unlink()
