"""YouTube Shorts video composition."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Sequence, Union

from ..utils.ffmpeg import FFmpegWrapper
from ..voiceover.models import VoiceSegment
from ..captions.timed import TimedCaptions
from .duration import DurationManager


@dataclass
class CompositionConfig:
    """Configuration for video composition."""
    width: int = 1080
    height: int = 1920
    fps: int = 30
    quality_preset: str = "medium"  # ultrafast, fast, medium, slow
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    target_duration: Optional[float] = None


class ShortsComposer:
    """Compose final YouTube Shorts video."""

    def __init__(self, ffmpeg: Optional[FFmpegWrapper] = None):
        """Initialize composer.

        Args:
            ffmpeg: FFmpeg wrapper (creates default if not provided)
        """
        self.ffmpeg = ffmpeg or FFmpegWrapper()
        self.duration_manager = DurationManager()

    def compose(
        self,
        source_clip: Union[Path, Sequence[Path]],
        voiceover_segments: List[VoiceSegment],
        captions: TimedCaptions,
        output_path: Path,
        config: Optional[CompositionConfig] = None
    ) -> Path:
        """Compose final Shorts video.

        Args:
            source_clip: Source video file
            voiceover_segments: List of voiceover audio segments
            captions: Timed captions
            output_path: Output video path
            config: Composition config (uses default if not provided)

        Returns:
            Path to composed video

        Raises:
            RuntimeError: If FFmpeg not available or composition fails
        """
        if not self.ffmpeg.is_available():
            raise RuntimeError("FFmpeg not available")

        config = config or CompositionConfig()

        # Create temp directory for intermediate files
        temp_dir = output_path.parent / f"temp_{output_path.stem}"
        temp_dir.mkdir(exist_ok=True)

        try:
            # Step 1: Merge voiceover segments into single audio
            merged_audio = self._merge_voiceover(voiceover_segments, temp_dir)
            if config.target_duration is not None:
                merged_audio = self._trim_audio(
                    merged_audio, config.target_duration, temp_dir
                )
                captions = captions.trim(config.target_duration)

            # Step 2: Save captions as ASS file
            caption_file = temp_dir / "captions.ass"
            captions.save_ass(caption_file)

            # Step 3: Convert source to Shorts format with audio and captions
            self._compose_final(
                source_clip=source_clip,
                audio_path=merged_audio,
                caption_file=caption_file,
                output_path=output_path,
                config=config
            )
            audio_duration = self.ffmpeg.probe_duration(merged_audio)
            video_duration = self.ffmpeg.probe_duration(output_path)
            self.duration_manager.verify(video_duration, audio_duration)
            if not self.ffmpeg.has_audio_stream(output_path):
                raise RuntimeError("Composed video does not contain the narration audio stream")

            return output_path

        finally:
            # Clean up temp files
            self._cleanup_temp(temp_dir)

    def _merge_voiceover(
        self,
        segments: List[VoiceSegment],
        temp_dir: Path
    ) -> Path:
        """Merge voiceover segments into single audio file.

        Args:
            segments: List of voiceover segments
            temp_dir: Temporary directory

        Returns:
            Path to merged audio file
        """
        if not segments:
            raise ValueError("No voiceover segments provided")

        # If single segment, just copy it
        if len(segments) == 1:
            merged = temp_dir / "merged_audio.mp3"
            with open(segments[0].audio_path, "rb") as src:
                with open(merged, "wb") as dst:
                    dst.write(src.read())
            return merged

        # Multiple segments - concatenate
        merged = temp_dir / "merged_audio.mp3"

        # Create concat file list
        concat_list = temp_dir / "concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for seg in segments:
                # Use forward slashes for FFmpeg concat file list on Windows
                safe_path = str(seg.audio_path.absolute()).replace('\\', '/')
                f.write(f"file '{safe_path}'\n")

        # Concat audio
        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(merged)
        ]

        returncode, _, stderr = self.ffmpeg.execute(args)

        if returncode != 0:
            raise RuntimeError(f"Audio merge failed: {stderr}")

        return merged

    def _trim_audio(
        self, audio_path: Path, target_duration: float, temp_dir: Path
    ) -> Path:
        """Trim narration audio to the configured maximum output duration."""
        if target_duration <= 0:
            raise ValueError("Target video duration must be positive")
        trimmed = temp_dir / "trimmed_audio.mp3"
        returncode, _, stderr = self.ffmpeg.execute(
            [
                "-i", str(audio_path),
                "-t", f"{target_duration:.6f}",
                "-c:a", "libmp3lame",
                "-q:a", "2",
                "-y",
                str(trimmed),
            ]
        )
        if returncode != 0:
            raise RuntimeError(f"Could not trim narration to target duration: {stderr}")
        return trimmed

    def _compose_final(
        self,
        source_clip: Union[Path, Sequence[Path]],
        audio_path: Path,
        caption_file: Path,
        output_path: Path,
        config: CompositionConfig
    ):
        """Compose final video with source, audio, and captions.

        Args:
            source_clip: Source video
            audio_path: Merged audio file
            caption_file: ASS caption file
            output_path: Output video path
            config: Composition config
        """
        if not isinstance(source_clip, Path):
            source_clips = list(source_clip)
            if not source_clips:
                raise ValueError("At least one source clip is required")
            if len(source_clips) > 1:
                self._compose_multiple_sources(
                    source_clips, audio_path, caption_file, output_path, config
                )
                return
            source_clip = source_clips[0]

        # Get source info
        info = self.ffmpeg.probe(source_clip)

        # Build filter chain
        filters = []

        filters.extend(self._orientation_filters(config))

        # Burn in captions
        # Ensure Windows paths are forward-slashed and colons are escaped for the ass filter
        safe_ass_path = str(caption_file).replace('\\', '/').replace(':', '\\:')
        caption_filter = f"ass='{safe_ass_path}'"
        filters.append(caption_filter)

        # Set FPS
        filters.append(f"fps={config.fps}")

        filter_complex = ",".join(filters)

        target_duration = (
            config.target_duration
            if config.target_duration is not None
            else self.ffmpeg.probe_duration(audio_path)
        )
        duration_plan = self.duration_manager.plan(info.duration, target_duration)

        # Loop only the video input when it cannot cover narration. Audio stays
        # a separate, single-pass input and is never looped.
        args = []
        if duration_plan.loop_video:
            args.extend(["-stream_loop", "-1"])
        args.extend([
            "-i", str(source_clip),
            "-i", str(audio_path),
            "-vf", filter_complex,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", config.video_codec,
            "-preset", config.quality_preset,
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-shortest",
            "-t", f"{duration_plan.target_duration:.6f}",
            "-y",
            str(output_path)
        ])

        returncode, _, stderr = self.ffmpeg.execute(args, timeout=600)

        if returncode != 0:
            raise RuntimeError(f"Video composition failed: {stderr}")

    def _compose_multiple_sources(
        self,
        source_clips: List[Path],
        audio_path: Path,
        caption_file: Path,
        output_path: Path,
        config: CompositionConfig,
    ) -> None:
        """Concatenate normalized source clips before captions and narration."""
        target_duration = (
            config.target_duration
            if config.target_duration is not None
            else self.ffmpeg.probe_duration(audio_path)
        )
        clip_infos = [self.ffmpeg.probe(clip) for clip in source_clips]
        total_source_duration = sum(info.duration for info in clip_infos)
        duration_plan = self.duration_manager.plan(
            total_source_duration, target_duration
        )

        included_clips = []
        remaining = target_duration
        for index, (clip, info) in enumerate(zip(source_clips, clip_infos)):
            if remaining <= 0:
                break
            if duration_plan.loop_video and index == len(source_clips) - 1:
                segment_duration = remaining
            else:
                segment_duration = min(info.duration, remaining)
            included_clips.append((clip, info, segment_duration))
            remaining -= min(info.duration, segment_duration)

        if not included_clips:
            raise ValueError("No usable source video segments were selected")

        filters = []
        video_labels = []
        args = []
        for index, (clip, info, segment_duration) in enumerate(included_clips):
            if duration_plan.loop_video and index == len(included_clips) - 1:
                args.extend(["-stream_loop", "-1"])
            args.extend(["-i", str(clip)])

            fit_filters = self._orientation_filters(config)

            label = f"clip{index}"
            video_labels.append(f"[{label}]")
            filters.append(
                f"[{index}:v:0]"
                + ",".join(
                    fit_filters
                    + [
                        f"trim=duration={segment_duration:.6f}",
                        "setpts=PTS-STARTPTS",
                        "fps={}".format(config.fps),
                        "setsar=1",
                    ]
                )
                + f"[{label}]"
            )

        concat_label = "joined"
        filters.append(
            "".join(video_labels)
            + f"concat=n={len(included_clips)}:v=1:a=0[{concat_label}]"
        )
        safe_ass_path = str(caption_file).replace("\\", "/").replace(":", "\\:")
        filters.append(
            f"[{concat_label}]ass='{safe_ass_path}'[captioned]"
        )
        audio_input_index = len(included_clips)
        args.extend(["-i", str(audio_path)])
        args.extend([
            "-filter_complex", ";".join(filters),
            "-map", "[captioned]",
            "-map", f"{audio_input_index}:a:0",
            "-c:v", config.video_codec,
            "-preset", config.quality_preset,
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-shortest",
            "-t", f"{duration_plan.target_duration:.6f}",
            "-y",
            str(output_path),
        ])

        returncode, _, stderr = self.ffmpeg.execute(args, timeout=600)
        if returncode != 0:
            raise RuntimeError(f"Video composition failed: {stderr}")

    @staticmethod
    def _orientation_filters(config: CompositionConfig) -> List[str]:
        return [
            f"scale={config.width}:{config.height}:force_original_aspect_ratio=decrease",
            f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2",
        ]

    def _cleanup_temp(self, temp_dir: Path):
        """Clean up temporary directory.

        Args:
            temp_dir: Temporary directory to remove
        """
        if temp_dir.exists():
            for file in temp_dir.iterdir():
                try:
                    file.unlink()
                except Exception:
                    pass
            try:
                temp_dir.rmdir()
            except Exception:
                pass
