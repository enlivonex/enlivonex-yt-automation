"""Add gentle UI zooms and animated infographic accents to the Enlivonex promo."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    AudioArrayClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    VideoFileClip,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "Enlivonex-Early-Access-Explainer-Vertical.mp4"
DEFAULT_OUTPUT = ROOT / "Enlivonex-Early-Access-Explainer-Animated.mp4"
FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920
UI_BOX = (20, 630, 1060, 1217)
BRAND_COLOR = (32, 201, 237)
CLICK_EVENTS = (
    (5.8, 620, 790, 1.42),
    (7.7, 320, 830, 1.42),
    (9.5, 325, 865, 1.42),
    (13.7, 600, 950, 1.35),
)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _make_pill(text: str, output: Path, font_path: Path) -> Image.Image:
    image = Image.new("RGBA", (570, 68), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2, 2, 568, 66),
        radius=23,
        fill=(9, 23, 39, 242),
        outline=(54, 181, 218, 180),
        width=2,
    )
    draw.ellipse((19, 24, 39, 44), fill=(*BRAND_COLOR, 255))
    draw.text(
        (53, 34),
        text,
        font=_font(font_path, 19),
        fill=(240, 247, 255, 255),
        anchor="lm",
    )
    image.save(output)
    return image


def _make_step_ring(output: Path) -> Image.Image:
    image = Image.new("RGBA", (70, 70), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((5, 5, 65, 65), outline=(*BRAND_COLOR, 220), width=3)
    draw.ellipse((11, 11, 59, 59), outline=(81, 194, 237, 80), width=1)
    image.save(output)
    return image


def _make_click_ripple() -> Image.Image:
    image = Image.new("RGBA", (112, 112), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((9, 9, 103, 103), outline=(*BRAND_COLOR, 230), width=3)
    draw.ellipse((19, 19, 93, 93), outline=(240, 249, 255, 190), width=2)
    draw.ellipse((39, 39, 73, 73), fill=(32, 201, 237, 80))
    return image


def _make_click_audio(
    base_audio,
) -> tuple[CompositeAudioClip, list[AudioArrayClip]]:
    sample_rate = 48_000
    effects: list[AudioArrayClip] = []
    rng = np.random.default_rng(20261105)

    for event_time, target_x, _target_y, _zoom in CLICK_EVENTS:
        whoosh_duration = 0.46
        whoosh_times = np.arange(
            round(whoosh_duration * sample_rate), dtype=np.float64
        ) / sample_rate
        white_noise = rng.normal(0.0, 1.0, len(whoosh_times))
        low_pass = np.convolve(white_noise, np.ones(61) / 61, mode="same")
        envelope = np.sin(np.pi * whoosh_times / whoosh_duration) ** 1.4
        sweep_frequency = 250 - 130 * whoosh_times / whoosh_duration
        sweep = np.sin(2 * np.pi * sweep_frequency * whoosh_times)
        whoosh = 0.07 * envelope * (low_pass * 2.6 + sweep * 0.35)
        pan = min(0.12, max(-0.12, (target_x / FRAME_WIDTH - 0.5) * 0.24))
        whoosh_stereo = np.column_stack(
            (whoosh * (1 - pan), whoosh * (1 + pan))
        ).astype(np.float32)
        whoosh_clip = AudioArrayClip(whoosh_stereo, fps=sample_rate).with_start(
            max(0, event_time - 0.28)
        )
        effects.append(whoosh_clip)

        click_duration = 0.095
        click_times = np.arange(round(click_duration * sample_rate)) / sample_rate
        pitch = 1450 - 760 * click_times / click_duration
        click = (
            0.11
            * np.sin(2 * np.pi * pitch * click_times)
            * np.exp(-click_times * 48)
        )
        click += (
            0.035
            * np.sin(2 * np.pi * 230 * click_times)
            * np.exp(-click_times * 38)
        )
        click_stereo = np.column_stack(
            (click * (1 - pan), click * (1 + pan))
        ).astype(np.float32)
        click_clip = AudioArrayClip(click_stereo, fps=sample_rate).with_start(
            event_time
        )
        effects.append(click_clip)

    return CompositeAudioClip([base_audio, *effects]), effects


def _with_animated_alpha(
    image: Image.Image,
    start: float,
    duration: float,
    position: tuple[int, int] | Callable[[float], tuple[float, float]],
    pulse: bool = False,
) -> ImageClip:
    pixels = np.asarray(image.convert("RGBA"))
    rgb = pixels[:, :, :3].copy()
    alpha = pixels[:, :, 3].astype(np.float32) / 255.0
    fade_duration = min(0.34, duration / 4)

    def mask_frame(time: float) -> np.ndarray:
        fade_in = min(1.0, max(0.0, time / fade_duration))
        fade_out = min(1.0, max(0.0, (duration - time) / fade_duration))
        opacity = min(fade_in, fade_out)
        if pulse:
            opacity *= 0.45 + 0.35 * (
                0.5 + 0.5 * math.sin(2 * math.pi * time / 1.8)
            )
        return alpha * opacity

    mask = VideoClip(
        frame_function=mask_frame,
        is_mask=True,
        duration=duration,
    )
    return (
        ImageClip(rgb, duration=duration)
        .with_mask(mask)
        .with_start(start)
        .with_position(position)
    )


def _make_zoomed_background(base: VideoFileClip) -> VideoClip:
    x1, y1, x2, y2 = UI_BOX
    panel_width, panel_height = x2 - x1, y2 - y1
    zoom_scenes = ((2.8, 13.3), (13.3, 33.8))

    def click_zoom(time: float) -> tuple[float, float, float]:
        for event_time, focus_x, focus_y, max_zoom in CLICK_EVENTS:
            local_time = time - event_time
            if -0.22 <= local_time <= 0.82:
                if local_time < 0:
                    progress = (local_time + 0.22) / 0.28
                elif local_time < 0.16:
                    progress = 1.0
                elif local_time < 0.72:
                    progress = 1.0 - (local_time - 0.16) / 0.56
                else:
                    progress = 0.0
                progress = min(1.0, max(0.0, progress))
                eased = progress * progress * (3 - 2 * progress)
                return 1 + (max_zoom - 1) * eased, focus_x, focus_y
        return 1.0, (x1 + x2) / 2, (y1 + y2) / 2

    def zoom_frame(time: float) -> np.ndarray:
        frame = base.get_frame(time).copy()
        scene = next(
            (
                (start, end)
                for start, end in zoom_scenes
                if start <= time < end
            ),
            None,
        )
        if scene is None:
            return frame

        scene_start, _ = scene
        panel = Image.fromarray(frame).crop(UI_BOX)
        local_time = time - scene_start
        breathing_zoom = 1.0 + 0.018 * (
            0.5 - 0.5 * math.cos(2 * math.pi * local_time / 7.5)
        )
        punch_zoom, focus_x, focus_y = click_zoom(time)
        scale = breathing_zoom * punch_zoom
        crop_width = panel_width / scale
        crop_height = panel_height / scale
        focus_x -= x1
        focus_y -= y1
        crop_left = min(
            max(0, focus_x - crop_width / 2),
            panel_width - crop_width,
        )
        crop_top = min(
            max(0, focus_y - crop_height / 2),
            panel_height - crop_height,
        )
        panel = panel.crop(
            (
                round(crop_left),
                round(crop_top),
                round(crop_left + crop_width),
                round(crop_top + crop_height),
            )
        )
        scaled_size = (
            panel_width,
            panel_height,
        )
        panel = panel.resize(scaled_size, Image.Resampling.LANCZOS)
        frame[y1:y2, x1:x2] = np.asarray(
            panel.crop((0, 0, panel_width, panel_height))
        )
        return frame

    return VideoClip(frame_function=zoom_frame, duration=base.duration)


def _add_scene_accents(
    layers: list[VideoClip],
    font_path: Path,
    assets_dir: Path,
) -> None:
    scenes = (
        (2.8, 10.5, "01  SHAPE YOUR STORY", 93),
        (13.3, 20.5, "02  WATCH THE DRAFT", 313),
    )
    pill_path = assets_dir / "scene-pill.png"
    ring_path = assets_dir / "step-ring.png"
    ring = _make_step_ring(ring_path)
    ripple = _make_click_ripple()

    for start, duration, label, step_x in scenes:
        pill = _make_pill(label, pill_path, font_path)

        def slide_position(time: float, y: int = 548) -> tuple[float, int]:
            amount = min(1.0, max(0.0, time / 0.42))
            eased = 1 - (1 - amount) ** 3
            return (-590 + 662 * eased, y)

        layers.append(
            _with_animated_alpha(
                pill,
                start=start + 0.2,
                duration=duration - 0.4,
                position=slide_position,
            )
        )
        layers.append(
            _with_animated_alpha(
                ring,
                start=start + 0.15,
                duration=duration - 0.3,
                position=(step_x - 35, 407),
                pulse=True,
            )
        )

    for event_time, target_x, target_y, _zoom in CLICK_EVENTS:
        layers.append(
            _with_animated_alpha(
                ripple,
                start=event_time - 0.12,
                duration=0.62,
                position=(target_x - 56, target_y - 56),
                pulse=True,
            )
        )


def render(input_path: Path, output_path: Path, gpu_encode: bool) -> None:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Base promo not found: {input_path}")

    font_path = Path(r"C:\Windows\Fonts\seguisb.ttf")
    if not font_path.is_file():
        raise FileNotFoundError(f"Required Windows UI font not found: {font_path}")

    base = VideoFileClip(str(input_path), audio=True)
    layers: list[VideoClip] = [base]
    try:
        if tuple(base.size) != (FRAME_WIDTH, FRAME_HEIGHT):
            raise ValueError(
                f"Expected a {FRAME_WIDTH}x{FRAME_HEIGHT} base video, got {base.size}."
            )
        if base.duration < 33.8:
            raise ValueError("The base promo is too short for the UI zoom scenes.")

        zoomed_background = _make_zoomed_background(base)
        layers[0] = zoomed_background
        with TemporaryDirectory(prefix="enlivonex-promo-") as assets_dir:
            _add_scene_accents(layers, font_path, Path(assets_dir))
            final_audio, audio_effects = _make_click_audio(base.audio)
        composite = CompositeVideoClip(
            layers,
            size=(FRAME_WIDTH, FRAME_HEIGHT),
            use_bgclip=True,
        ).with_audio(final_audio)
        composite.duration = base.duration
        composite.end = base.end

        ffmpeg_params = ["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        codec = "libx264"
        preset = "medium"
        if gpu_encode:
            codec = "h264_nvenc"
            preset = "p4"
            ffmpeg_params = ["-cq", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]

        composite.write_videofile(
            str(output_path),
            fps=30,
            codec=codec,
            audio_codec="aac",
            audio_fps=48000,
            audio_bitrate="192k",
            preset=preset,
            threads=4,
            ffmpeg_params=ffmpeg_params,
            logger="bar",
        )
        composite.close()
    finally:
        base.close()
        for layer in layers:
            layer.close()
        if "final_audio" in locals():
            final_audio.close()
        if "audio_effects" in locals():
            for effect in audio_effects:
                effect.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--gpu-encode",
        action="store_true",
        help="Use NVENC when the installed NVIDIA driver supports the FFmpeg build.",
    )
    args = parser.parse_args()
    render(args.input, args.output, args.gpu_encode)


if __name__ == "__main__":
    main()
