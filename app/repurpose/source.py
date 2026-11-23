"""Read-only discovery and probing of local long-form video sources."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from threading import Event

from ..utils.ffmpeg import FFmpegWrapper
from .cancellation import raise_if_cancelled
from .models import VideoSource

SUPPORTED_VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
)
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class VideoScanFailure:
    """A source file that could not be inspected."""

    path: Path
    message: str


@dataclass(frozen=True)
class VideoScanResult:
    """Successful source inspections and per-file failures."""

    sources: tuple[VideoSource, ...]
    failures: tuple[VideoScanFailure, ...]


class LocalVideoCatalog:
    """Find and inspect local videos without moving or modifying them."""

    def __init__(self, ffmpeg: FFmpegWrapper | None = None):
        self.ffmpeg = ffmpeg or FFmpegWrapper()

    def scan(
        self, directory: Path, recursive: bool = False
    ) -> VideoScanResult:
        """Inspect supported videos, reporting failures instead of hiding them."""
        root = Path(directory).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"Video folder not found: {root}")
        root = root.resolve()
        pattern = "**/*" if recursive else "*"
        paths = sorted(
            path
            for path in root.glob(pattern)
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
            and not path.is_symlink()
        )
        if not paths:
            return VideoScanResult((), ())

        sources = []
        failures = []
        for path in paths:
            try:
                sources.append(self.inspect(path))
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(VideoScanFailure(path, str(error)))
        return VideoScanResult(tuple(sources), tuple(failures))

    def inspect(
        self, path: Path, cancel_event: Event | None = None
    ) -> VideoSource:
        """Probe one supported video and compute its content hash."""
        raise_if_cancelled(cancel_event)
        source_path = Path(path).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(f"Video file not found: {source_path}")
        if source_path.is_symlink():
            raise ValueError("Symbolic-link video sources are not supported.")
        if source_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError(
                f"Unsupported video format: {source_path.suffix or '(no extension)'}"
            )

        source_path = source_path.resolve()
        file_size = source_path.stat().st_size
        if file_size <= 0:
            raise ValueError(f"Video file is empty: {source_path.name}")

        info = self.ffmpeg.probe(source_path)
        raise_if_cancelled(cancel_event)
        digest = hashlib.sha256()
        with source_path.open("rb") as source_file:
            while chunk := source_file.read(HASH_CHUNK_BYTES):
                raise_if_cancelled(cancel_event)
                digest.update(chunk)
        raise_if_cancelled(cancel_event)

        return VideoSource(
            path=source_path,
            sha256=digest.hexdigest(),
            file_size_bytes=file_size,
            duration_seconds=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
            codec=info.codec,
            aspect_ratio=info.aspect_ratio,
        )
