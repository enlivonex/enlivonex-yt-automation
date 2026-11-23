"""Media validation."""

import mimetypes
from pathlib import Path
from typing import Optional, Tuple
from app.utils.logger import get_logger

logger = get_logger()


class MediaValidator:
    """Validates media files before processing."""

    def __init__(
        self,
        supported_image_formats: list[str],
        supported_video_formats: list[str],
        max_file_size_mb: int
    ):
        """Initialize validator with supported formats."""
        self.supported_image_formats = [f.lower() for f in supported_image_formats]
        self.supported_video_formats = [f.lower() for f in supported_video_formats]
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024

    def is_supported_format(self, filename: str) -> bool:
        """Check if file format is supported."""
        ext = Path(filename).suffix.lower().lstrip(".")
        return ext in self.supported_image_formats or ext in self.supported_video_formats

    def is_image(self, filename: str) -> bool:
        """Check if file is an image."""
        ext = Path(filename).suffix.lower().lstrip(".")
        return ext in self.supported_image_formats

    def is_video(self, filename: str) -> bool:
        """Check if file is a video."""
        ext = Path(filename).suffix.lower().lstrip(".")
        return ext in self.supported_video_formats

    def validate_file(self, file_path: Path) -> Tuple[bool, Optional[str]]:
        """Validate a media file.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check file exists
        if not file_path.exists():
            return False, f"File does not exist: {file_path}"

        # Check file size
        file_size = file_path.stat().st_size
        if file_size > self.max_file_size_bytes:
            size_mb = file_size / (1024 * 1024)
            max_mb = self.max_file_size_bytes / (1024 * 1024)
            return False, f"File too large: {size_mb:.1f}MB (max: {max_mb}MB)"

        # Check file format
        if not self.is_supported_format(file_path.name):
            return False, f"Unsupported format: {file_path.suffix}"

        # Check if file is readable
        try:
            with open(file_path, "rb") as f:
                f.read(1024)
        except Exception as e:
            return False, f"Cannot read file: {e}"

        logger.debug(f"Validated file: {file_path.name}")
        return True, None

    def get_media_type(self, filename: str) -> Optional[str]:
        """Get media type (image/video) or None if unsupported."""
        if self.is_image(filename):
            return "image"
        elif self.is_video(filename):
            return "video"
        return None
