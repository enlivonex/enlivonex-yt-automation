"""Local FFmpeg renderer for reviewed long-video clip selections."""

import math
import os
from pathlib import Path
import tempfile
from threading import Event
from typing import Callable

from ..captions.timed import (
    CaptionSegment,
    CaptionStyle,
    CaptionWord,
    TimedCaptions,
)
from ..utils.ffmpeg import FFmpegWrapper, VideoInfo
from .cancellation import raise_if_cancelled
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

    def render(
        self,
        plan: ClipEditPlan,
        output_path: Path,
        *,
        cancel_event: Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> RenderedClip:
        """Render a compilation without modifying or replacing source media."""
        raise_if_cancelled(cancel_event)
        if cancel_event is None:
            source = self.catalog.inspect(plan.source.path)
        else:
            source = self.catalog.inspect(
                plan.source.path, cancel_event=cancel_event
            )
        if source.sha256 != plan.source.sha256:
            raise RuntimeError("Source video changed after its analysis.")
        raise_if_cancelled(cancel_event)

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
            total_duration = sum(
                clip_range.end_seconds - clip_range.start_seconds
                for clip_range in plan.ranges
            )
            for index, clip_range in enumerate(plan.ranges):
                raise_if_cancelled(cancel_event)
                duration = clip_range.end_seconds - clip_range.start_seconds
                if stage_callback is not None:
                    stage_callback(
                        f"Rendering moment {index + 1} of {len(plan.ranges)}..."
                    )
                segment_path = working_directory / f"segment_{index:04d}.mp4"
                caption_path = None
                if plan.settings.burn_captions or plan.settings.creator_credit.strip():
                    caption_path = working_directory / f"captions_{index:04d}.ass"
                    self._write_segment_captions(
                        plan, clip_range, caption_path
                    )
                segment_progress = None
                if progress_callback is not None:
                    prior_duration = offset_seconds

                    def segment_progress(
                        fraction: float,
                        prior: float = prior_duration,
                        segment_duration: float = duration,
                    ) -> None:
                        progress_callback(
                            0.9
                            * (prior + segment_duration * fraction)
                            / total_duration
                        )

                self._render_segment(
                    source.path,
                    clip_range,
                    segment_path,
                    caption_path,
                    has_audio,
                    plan,
                    index,
                    cancel_event,
                    segment_progress,
                )
                segment_paths.append(segment_path)
                offset_seconds += duration

            raise_if_cancelled(cancel_event)
            if stage_callback is not None:
                stage_callback("Joining rendered moments...")
            concat_list = working_directory / "segments.ffconcat"
            self._write_concat_list(concat_list, segment_paths)
            concat_progress = None
            if progress_callback is not None:
                concat_progress = lambda fraction: progress_callback(
                    0.9 + 0.09 * fraction
                )
            self._concatenate(
                concat_list,
                temporary_output,
                total_duration,
                cancel_event,
                concat_progress,
            )
            raise_if_cancelled(cancel_event)
            if stage_callback is not None:
                stage_callback("Validating rendered draft...")
            if progress_callback is not None:
                progress_callback(0.99)
            rendered = self._validate_output(
                temporary_output,
                plan,
                offset_seconds,
                has_audio,
            )
            raise_if_cancelled(cancel_event)
            if destination.exists():
                raise FileExistsError(f"Output video already exists: {destination}")
            os.replace(temporary_output, destination)
            if progress_callback is not None:
                progress_callback(1.0)
            if stage_callback is not None:
                stage_callback("Draft ready.")
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
        cancel_event: Event | None,
        progress_callback: Callable[[float], None] | None,
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
        if (
            settings.brightness != 0.0
            or settings.contrast != 1.0
            or settings.saturation != 1.0
        ):
            filters.append(
                f"eq=brightness={settings.brightness:g}:"
                f"contrast={settings.contrast:g}:"
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
        args.extend(["-movflags", "+faststart"])
        if cancel_event is not None or progress_callback is not None:
            args.extend(["-progress", "pipe:1", "-nostats", str(segment_path)])
            return_code, _, stderr = self.ffmpeg.execute_cancellable(
                args,
                timeout=RENDER_TIMEOUT_SECONDS,
                expected_duration_seconds=duration,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        else:
            args.append(str(segment_path))
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
        if fit_mode == "blurred":
            return (
                f"split=2[background][foreground];"
                f"[background]scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=20:2,"
                "eq=brightness=-0.18:saturation=0.7[background];"
                f"[foreground]scale={width}:{height}:"
                "force_original_aspect_ratio=decrease[foreground];"
                "[background][foreground]overlay=(W-w)/2:(H-h)/2"
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
        gaming_style = plan.settings.caption_style == "gaming"
        captions = []
        for transcript_segment in plan.transcript:
            start = max(transcript_segment.start_seconds, clip_range.start_seconds)
            end = min(transcript_segment.end_seconds, clip_range.end_seconds)
            if end <= start:
                continue
            caption_words = []
            for word in transcript_segment.words:
                word_start = max(word.start_seconds, clip_range.start_seconds)
                word_end = min(word.end_seconds, clip_range.end_seconds)
                if word_end > word_start:
                    caption_words.append(
                        CaptionWord(
                            word=word.text,
                            start_time=word_start - clip_range.start_seconds,
                            end_time=word_end - clip_range.start_seconds,
                        )
                    )
            if caption_words:
                for index in range(0, len(caption_words), 4):
                    word_group = caption_words[index:index + 4]
                    captions.append(
                        CaptionSegment(
                            words=word_group,
                            text=" ".join(word.word for word in word_group),
                            start_time=word_group[0].start_time,
                            end_time=word_group[-1].end_time,
                        )
                    )
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
        caption_style = CaptionStyle(font_size=plan.settings.caption_font_size)
        if gaming_style:
            caption_style.font_color = "#FFE600"
            caption_style.outline_width = 4
            caption_style.uppercase = False
            if any(caption.words for caption in captions):
                caption_style.font_color = "#FFFFFF"
                caption_style.secondary_color = "#FFE600"
                caption_style.word_highlight = True
        timed_captions = TimedCaptions(
            segments=captions,
            style=caption_style,
            total_duration=duration,
        )
        ass_text = timed_captions.to_ass().replace(
            "ScriptType: v4.00+",
            f"ScriptType: v4.00+\nPlayResX: {plan.settings.width}\n"
            f"PlayResY: {plan.settings.height}",
        )
        if gaming_style:
            caption_y = round(plan.settings.height * 0.72)
            positioning = (
                r"{\an5\pos("
                f"{plan.settings.width // 2},{caption_y}"
                r")}"
            )
            ass_text = ass_text.replace(
                ",Default,", f",Default,{positioning}"
            )
        creator_credit = plan.settings.creator_credit.strip()
        if creator_credit:
            credit_text = f"Credits: {creator_credit}"
            credit_text = credit_text.replace("\\", "\\\\")
            credit_text = credit_text.replace("{", "\\{").replace("}", "\\}")
            credit_style = (
                f"Style: Credit,Arial,{max(20, plan.settings.caption_font_size // 2)},"
                "&H00FFFFFF,&H00000000,&H64000000,-1,2,8\n"
            )
            credit_event = (
                f"Dialogue: 0:00:00.00,"
                f"{TimedCaptions._format_ass_timestamp(duration)},"
                f"Credit,{credit_text}\n"
            )
            ass_text = ass_text.replace(
                "[Events]", f"{credit_style}\n[Events]"
            ) + "\n" + credit_event
        caption_path.write_text(ass_text, encoding="utf-8")

    @staticmethod
    def _write_concat_list(path: Path, segment_paths: list[Path]) -> None:
        lines = ["ffconcat version 1.0"]
        for segment_path in segment_paths:
            ffconcat_path = str(segment_path.resolve()).replace("\\", "/")
            ffconcat_path = ffconcat_path.replace("'", "'\\''")
            lines.append(f"file '{ffconcat_path}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _concatenate(
        self,
        concat_list: Path,
        output_path: Path,
        expected_duration: float,
        cancel_event: Event | None,
        progress_callback: Callable[[float], None] | None,
    ) -> None:
        args = [
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
        ]
        if cancel_event is not None or progress_callback is not None:
            args.extend(["-progress", "pipe:1", "-nostats", str(output_path)])
            return_code, _, stderr = self.ffmpeg.execute_cancellable(
                args,
                timeout=RENDER_TIMEOUT_SECONDS,
                expected_duration_seconds=expected_duration,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        else:
            args.append(str(output_path))
            return_code, _, stderr = self.ffmpeg.execute(
                args, timeout=RENDER_TIMEOUT_SECONDS
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
