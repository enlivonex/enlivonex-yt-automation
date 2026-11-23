"""Local FFmpeg renderer for reviewed long-video clip selections."""

import math
import os
from pathlib import Path
import tempfile

from ..captions.timed import CaptionSegment, CaptionStyle, TimedCaptions
from ..utils.ffmpeg import FFmpegWrapper, VideoInfo
from .editing import ClipEditPlan, ClipRange, RenderedClip
from .source import LocalVideoCatalog

RENDER_TIMEOUT_SECONDS = 3600
DURATION_TOLERANCE_SECONDS = 0.75


class LocalClipRenderer:
    """Render selected source ranges into one validated output video."""

    def __init__(
        self,
        ffmpeg: FFmpegWrapper | None = None,
        catalog: LocalVideoCatalog | None = None,
    ):
        self.ffmpeg = ffmpeg or FFmpegWrapper()
        self.catalog = catalog or LocalVideoCatalog(self.ffmpeg)

    def render(self, plan: ClipEditPlan, output_path: Path) -> RenderedClip:
        """Render a compilation without modifying or replacing source media."""
        source = self.catalog.inspect(plan.source.path)
        if source.sha256 != plan.source.sha256:
            raise RuntimeError("Source video changed after its analysis.")

        destination = Path(output_path).expanduser().resolve()
        if destination == source.path:
            raise ValueError("Output video must not overwrite the source video.")
        if destination.suffix.lower() != ".mp4":
            raise ValueError("Rendered output path must use the .mp4 extension.")
        if destination.exists():
            raise FileExistsError(f"Output video already exists: {destination}")
        if not self.ffmpeg.is_available():
            raise RuntimeError("FFmpeg/FFprobe is required to render clips.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        working_directory = Path(
            tempfile.mkdtemp(prefix=".enlivonex-render-", dir=destination.parent)
        )
        temporary_output = working_directory / "compiled.mp4"
        try:
            has_audio = self.ffmpeg.has_audio_stream(source.path)
            segment_paths = []
            offset_seconds = 0.0
            for index, clip_range in enumerate(plan.ranges):
                segment_path = working_directory / f"segment_{index:04d}.mp4"
                caption_path = None
                if plan.settings.burn_captions:
                    caption_path = working_directory / f"captions_{index:04d}.ass"
                    self._write_segment_captions(
                        plan, clip_range, caption_path
                    )
                self._render_segment(
                    source.path,
                    clip_range,
                    segment_path,
                    caption_path,
                    has_audio,
                    plan,
                    index,
                )
                segment_paths.append(segment_path)
                offset_seconds += clip_range.end_seconds - clip_range.start_seconds

            concat_list = working_directory / "segments.ffconcat"
            self._write_concat_list(concat_list, segment_paths)
            self._concatenate(concat_list, temporary_output)
            rendered = self._validate_output(
                temporary_output,
                plan,
                offset_seconds,
                has_audio,
            )
            if destination.exists():
                raise FileExistsError(f"Output video already exists: {destination}")
            os.replace(temporary_output, destination)
            return RenderedClip(
                path=destination,
                duration_seconds=rendered.duration,
                width=rendered.width,
                height=rendered.height,
                has_audio=has_audio,
                source_range_count=len(plan.ranges),
            )
        finally:
            for path in working_directory.iterdir():
                path.unlink(missing_ok=True)
            working_directory.rmdir()

    def _render_segment(
        self,
        source_path: Path,
        clip_range: ClipRange,
        segment_path: Path,
        caption_path: Path | None,
        has_audio: bool,
        plan: ClipEditPlan,
        index: int,
    ) -> None:
        settings = plan.settings
        filters = [
            self._fit_filter(settings.width, settings.height, settings.fit_mode),
            "setsar=1",
            f"fps={settings.fps}",
        ]
        if settings.sharpness:
            filters.append(
                "unsharp=luma_msize_x=5:luma_msize_y=5:"
                f"luma_amount={settings.sharpness:g}:"
                "chroma_msize_x=5:chroma_msize_y=5:chroma_amount=0"
            )
        if settings.contrast != 1.0 or settings.saturation != 1.0:
            filters.append(
                f"eq=contrast={settings.contrast:g}:"
                f"saturation={settings.saturation:g}"
            )
        if settings.grain:
            filters.append(f"noise=alls={settings.grain}:allf=t+u")
        if caption_path:
            filters.append(self._ass_filter(caption_path))

        duration = clip_range.end_seconds - clip_range.start_seconds
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{clip_range.start_seconds:.6f}",
            "-i",
            str(source_path),
            "-t",
            f"{duration:.6f}",
            "-vf",
            ",".join(filters),
            "-map",
            "0:v:0",
            "-c:v",
            "libx264",
            "-preset",
            settings.quality_preset,
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(settings.fps),
        ]
        if has_audio:
            args.extend(
                ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
            )
        else:
            args.append("-an")
        args.extend(["-movflags", "+faststart", str(segment_path)])

        return_code, _, stderr = self.ffmpeg.execute(
            args, timeout=RENDER_TIMEOUT_SECONDS
        )
        if return_code != 0:
            detail = stderr.strip()[-1200:] or "FFmpeg returned a non-zero exit code."
            raise RuntimeError(f"Could not render clip {index + 1}: {detail}")

    @staticmethod
    def _fit_filter(width: int, height: int, fit_mode: str) -> str:
        if fit_mode == "crop":
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )

    @staticmethod
    def _ass_filter(path: Path) -> str:
        safe_path = str(path.resolve()).replace("\\", "/").replace(":", "\\:")
        safe_path = safe_path.replace("'", "\\'")
        return f"ass='{safe_path}'"

    @staticmethod
    def _write_segment_captions(
        plan: ClipEditPlan,
        clip_range: ClipRange,
        caption_path: Path,
    ) -> None:
        captions = []
        for transcript_segment in plan.transcript:
            start = max(transcript_segment.start_seconds, clip_range.start_seconds)
            end = min(transcript_segment.end_seconds, clip_range.end_seconds)
            if end <= start:
                continue
            text = transcript_segment.text.replace("\\", "\\\\")
            text = text.replace("{", "\\{").replace("}", "\\}")
            captions.append(
                CaptionSegment(
                    words=[],
                    text=text,
                    start_time=start - clip_range.start_seconds,
                    end_time=end - clip_range.start_seconds,
                )
            )
        duration = clip_range.end_seconds - clip_range.start_seconds
        timed_captions = TimedCaptions(
            segments=captions,
            style=CaptionStyle(font_size=plan.settings.caption_font_size),
            total_duration=duration,
        )
        ass_text = timed_captions.to_ass().replace(
            "ScriptType: v4.00+",
            f"ScriptType: v4.00+\nPlayResX: {plan.settings.width}\n"
            f"PlayResY: {plan.settings.height}",
        )
        caption_path.write_text(ass_text, encoding="utf-8")

    @staticmethod
    def _write_concat_list(path: Path, segment_paths: list[Path]) -> None:
        lines = ["ffconcat version 1.0"]
        for segment_path in segment_paths:
            ffconcat_path = str(segment_path.resolve()).replace("\\", "/")
            ffconcat_path = ffconcat_path.replace("'", "'\\''")
            lines.append(f"file '{ffconcat_path}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _concatenate(self, concat_list: Path, output_path: Path) -> None:
        return_code, _, stderr = self.ffmpeg.execute(
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        if return_code != 0:
            detail = stderr.strip()[-1200:] or "FFmpeg returned a non-zero exit code."
            raise RuntimeError(f"Could not join rendered clips: {detail}")

    def _validate_output(
        self,
        output_path: Path,
        plan: ClipEditPlan,
        expected_duration: float,
        expected_audio: bool,
    ) -> VideoInfo:
        info = self.ffmpeg.probe(output_path)
        if info.width != plan.settings.width or info.height != plan.settings.height:
            raise RuntimeError(
                "Rendered video dimensions do not match the requested output."
            )
        if not math.isfinite(info.duration) or abs(
            info.duration - expected_duration
        ) > DURATION_TOLERANCE_SECONDS:
            raise RuntimeError(
                "Rendered video duration does not match the selected clip ranges: "
                f"expected {expected_duration:.3f}s, got {info.duration:.3f}s."
            )
        has_audio = self.ffmpeg.has_audio_stream(output_path)
        if has_audio != expected_audio:
            raise RuntimeError(
                "Rendered video audio stream does not match the source video."
            )
        return info
