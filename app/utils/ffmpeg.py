"""FFmpeg wrapper for direct subprocess control.

Replaces the unmaintained ffmpeg-python library with direct subprocess calls.
Provides a clean interface for video processing operations.
"""

import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

from .process import hidden_process_creation_flags


@dataclass
class VideoInfo:
    """Video file information from FFprobe."""
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    bitrate: Optional[int]
    aspect_ratio: str

    @property
    def orientation(self) -> str:
        """Determine video orientation."""
        if self.width > self.height:
            return "landscape"
        elif self.height > self.width:
            return "portrait"
        else:
            return "square"


class FFmpegWrapper:
    """Wrapper for FFmpeg and FFprobe operations."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """Initialize FFmpeg wrapper.

        Args:
            ffmpeg_path: Path to ffmpeg binary
            ffprobe_path: Path to ffprobe binary
        """
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._available = None

    def is_available(self) -> bool:
        """Check if FFmpeg is available.

        Returns:
            True if both ffmpeg and ffprobe are available
        """
        if self._available is not None:
            return self._available

        try:
            # Check ffmpeg
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                timeout=5,
                creationflags=hidden_process_creation_flags(),
            )
            if result.returncode != 0:
                self._available = False
                return False

            # Check ffprobe
            result = subprocess.run(
                [self.ffprobe_path, "-version"],
                capture_output=True,
                timeout=5,
                creationflags=hidden_process_creation_flags(),
            )
            self._available = result.returncode == 0
            return self._available

        except (subprocess.SubprocessError, FileNotFoundError):
            self._available = False
            return False

    def probe(self, input_path: Path) -> VideoInfo:
        """Get video information using FFprobe.

        Args:
            input_path: Path to video file

        Returns:
            VideoInfo object

        Raises:
            RuntimeError: If FFprobe fails
        """
        if not self.is_available():
            raise RuntimeError("FFmpeg/FFprobe not available")

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(input_path)
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                creationflags=hidden_process_creation_flags(),
            )

            data = json.loads(result.stdout)

            # Find video stream
            video_stream = next(
                (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
                None
            )

            if not video_stream:
                raise RuntimeError("No video stream found")

            # Parse FPS
            fps_parts = video_stream.get("r_frame_rate", "30/1").split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else 30.0

            # Get aspect ratio
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            if width and height:
                from math import gcd
                divisor = gcd(width, height)
                aspect_ratio = f"{width//divisor}:{height//divisor}"
            else:
                aspect_ratio = "unknown"

            return VideoInfo(
                duration=float(data.get("format", {}).get("duration", 0)),
                width=width,
                height=height,
                fps=fps,
                codec=video_stream.get("codec_name", "unknown"),
                bitrate=int(data.get("format", {}).get("bit_rate", 0)) if data.get("format", {}).get("bit_rate") else None,
                aspect_ratio=aspect_ratio
            )

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFprobe failed: {e.stderr}")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise RuntimeError(f"Failed to parse FFprobe output: {e}")

    def probe_duration(self, input_path: Path) -> float:
        """Return media duration from FFprobe, including audio-only files."""
        if not self.is_available():
            raise RuntimeError("FFmpeg/FFprobe not available")

        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path)
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                creationflags=hidden_process_creation_flags(),
            )
            duration = float(result.stdout.strip())
            if duration <= 0:
                raise ValueError("Media duration must be positive")
            return duration
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFprobe failed for {input_path}: {e.stderr}")
        except (ValueError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"Failed to read media duration for {input_path}: {e}")

    def has_audio_stream(self, input_path: Path) -> bool:
        """Return whether FFprobe finds an audio stream in the media file."""
        if not self.is_available():
            raise RuntimeError("FFmpeg/FFprobe not available")

        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(input_path)
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                creationflags=hidden_process_creation_flags(),
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFprobe failed for {input_path}: {e.stderr}")
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"FFprobe timed out for {input_path}: {e}")

    def execute(self, args: List[str], timeout: int = 300) -> Tuple[int, str, str]:
        """Execute FFmpeg command.

        Args:
            args: FFmpeg arguments (without ffmpeg binary path)
            timeout: Command timeout in seconds

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if not self.is_available():
            raise RuntimeError("FFmpeg not available")

        cmd = [self.ffmpeg_path] + args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=hidden_process_creation_flags(),
            )
            return result.returncode, result.stdout, result.stderr

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"FFmpeg command timed out after {timeout}s")
        except Exception as e:
            raise RuntimeError(f"FFmpeg execution failed: {e}")

    def convert_to_shorts(
        self,
        input_path: Path,
        output_path: Path,
        target_duration: Optional[float] = None,
        audio_path: Optional[Path] = None,
        caption_file: Optional[Path] = None
    ) -> bool:
        """Convert video to YouTube Shorts format (9:16).

        Args:
            input_path: Source video path
            output_path: Output video path
            target_duration: Target duration in seconds (trim/loop if needed)
            audio_path: Optional audio file to mix in
            caption_file: Optional SRT/VTT subtitle file

        Returns:
            True if successful
        """
        if not self.is_available():
            raise RuntimeError("FFmpeg not available")

        # Get source info
        info = self.probe(input_path)

        # Build filter chain
        filters = []

        # Scale and crop to 9:16 (1080x1920 for Shorts)
        if info.orientation == "landscape":
            # Crop landscape to portrait
            filters.append("scale=1920:1920:force_original_aspect_ratio=decrease")
            filters.append("crop=1080:1920")
        elif info.orientation == "square":
            # Letterbox square to portrait
            filters.append("scale=1080:1080")
            filters.append("pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
        else:
            # Already portrait, just scale
            filters.append("scale=1080:1920:force_original_aspect_ratio=decrease")
            filters.append("pad=1080:1920:(ow-iw)/2:(oh-ih)/2")

        # Handle duration
        if target_duration:
            if target_duration < info.duration:
                # Trim
                trim_filter = f"trim=duration={target_duration},setpts=PTS-STARTPTS"
                filters.insert(0, trim_filter)
            elif target_duration > info.duration:
                # Loop
                loops = int(target_duration / info.duration) + 1
                filters.insert(0, f"loop=loop={loops}:size=1:start=0")

        # Burn captions if provided
        if caption_file and caption_file.exists():
            # Convert Windows path to forward slashes for FFmpeg
            caption_path = str(caption_file).replace('\\', '/')
            caption_filter = f"subtitles={caption_path}:force_style='FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2'"
            filters.append(caption_filter)

        filter_complex = ",".join(filters)

        # Build command
        args = [
            "-i", str(input_path),
            "-filter_complex", filter_complex,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", "30"
        ]

        # Handle audio
        if audio_path and audio_path.exists():
            args.extend(["-i", str(audio_path)])
            args.extend(["-c:a", "aac", "-b:a", "192k"])
            args.extend(["-map", "0:v:0", "-map", "1:a:0"])
        else:
            if info.duration > 0:  # Has audio
                args.extend(["-c:a", "copy"])

        # Output
        args.extend(["-y", str(output_path)])

        returncode, stdout, stderr = self.execute(args)

        if returncode != 0:
            raise RuntimeError(f"FFmpeg conversion failed: {stderr}")

        return True
