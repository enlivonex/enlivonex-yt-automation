"""Media processing and caption rendering."""

import subprocess
from pathlib import Path
from typing import Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
from app.utils.logger import get_logger
from app.utils.process import hidden_process_creation_flags

logger = get_logger()


class MediaProcessor:
    """Processes media files by rendering captions."""

    def __init__(
        self,
        caption_font_size: int,
        caption_padding: int,
        caption_bg_color: Tuple[int, int, int, int],
        caption_text_color: Tuple[int, int, int, int],
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe"
    ):
        """Initialize media processor."""
        self.caption_font_size = caption_font_size
        self.caption_padding = caption_padding
        self.caption_bg_color = caption_bg_color
        self.caption_text_color = caption_text_color
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def process_image(
        self,
        input_path: Path,
        output_path: Path,
        caption: str
    ) -> bool:
        """Render caption on image.

        Args:
            input_path: Path to input image
            output_path: Path to save output image
            caption: Caption text to render

        Returns:
            True if successful, False otherwise
        """
        try:
            # Open image
            img = Image.open(input_path)

            # Convert to RGBA if needed for transparency support
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # Create drawing context
            draw = ImageDraw.Draw(img)

            # Load font (try to use a good default, fallback to default)
            try:
                font = ImageFont.truetype("arial.ttf", self.caption_font_size)
            except:
                try:
                    font = ImageFont.truetype("Arial.ttf", self.caption_font_size)
                except:
                    font = ImageFont.load_default()
                    logger.warning("Using default font; install Arial for better results")

            # Calculate text size and wrapping
            wrapped_lines = self._wrap_text(caption, img.width - 2 * self.caption_padding, font, draw)

            # Calculate total text block height
            line_heights = [draw.textbbox((0, 0), line, font=font)[3] for line in wrapped_lines]
            total_text_height = sum(line_heights) + (len(wrapped_lines) - 1) * 10

            # Calculate caption box dimensions and position
            box_height = total_text_height + 2 * self.caption_padding
            box_y = img.height - box_height - 20  # 20px from bottom

            # Create semi-transparent overlay
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            # Draw rounded rectangle background
            box_coords = [
                (self.caption_padding, box_y),
                (img.width - self.caption_padding, box_y + box_height)
            ]
            overlay_draw.rounded_rectangle(
                box_coords,
                radius=15,
                fill=self.caption_bg_color
            )

            # Composite overlay onto image
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)

            # Draw text lines
            current_y = box_y + self.caption_padding
            for line, line_height in zip(wrapped_lines, line_heights):
                # Center text horizontally
                text_bbox = draw.textbbox((0, 0), line, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_x = (img.width - text_width) // 2

                # Draw text
                draw.text(
                    (text_x, current_y),
                    line,
                    font=font,
                    fill=self.caption_text_color
                )
                current_y += line_height + 10

            # Save output
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert back to RGB for formats that don't support alpha
            if output_path.suffix.lower() in [".jpg", ".jpeg"]:
                rgb_img = Image.new("RGB", img.size, (0, 0, 0))
                rgb_img.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
                rgb_img.save(output_path, quality=95)
            else:
                img.save(output_path)

            logger.info(f"Processed image: {input_path.name} -> {output_path.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to process image {input_path}: {e}")
            return False

    def process_video(
        self,
        input_path: Path,
        output_path: Path,
        caption: str
    ) -> bool:
        """Render caption on video using FFmpeg.

        Args:
            input_path: Path to input video
            output_path: Path to save output video
            caption: Caption text to render

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get video dimensions
            probe_cmd = [
                self.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(input_path)
            ]

            result = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True,
                check=True,
                creationflags=hidden_process_creation_flags(),
            )

            width, height = map(int, result.stdout.strip().split(","))
            logger.debug(f"Video dimensions: {width}x{height}")

            # Escape caption text for FFmpeg
            escaped_caption = caption.replace("'", "'\\\\\\''").replace(":", "\\:")

            # Calculate font size relative to video height
            font_size = max(int(height * 0.05), 20)

            # Create drawtext filter
            # Position at bottom with padding, centered horizontally
            box_y = height - 120

            drawtext_filter = (
                f"drawtext="
                f"text='{escaped_caption}':"
                f"fontsize={font_size}:"
                f"fontcolor=white:"
                f"x=(w-text_w)/2:"
                f"y={box_y}:"
                f"box=1:"
                f"boxcolor=black@0.8:"
                f"boxborderw=15"
            )

            # Build FFmpeg command
            output_path.parent.mkdir(parents=True, exist_ok=True)

            ffmpeg_cmd = [
                self.ffmpeg_path,
                "-i", str(input_path),
                "-vf", drawtext_filter,
                "-codec:a", "copy",  # Copy audio without re-encoding
                "-y",  # Overwrite output
                str(output_path)
            ]

            logger.debug(f"Running FFmpeg command: {' '.join(ffmpeg_cmd)}")

            # Run FFmpeg
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                creationflags=hidden_process_creation_flags(),
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                return False

            logger.info(f"Processed video: {input_path.name} -> {output_path.name}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg timeout processing {input_path}")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Failed to process video {input_path}: {e}")
            return False

    def _wrap_text(
        self,
        text: str,
        max_width: int,
        font: ImageFont.FreeTypeFont,
        draw: ImageDraw.ImageDraw
    ) -> list[str]:
        """Wrap text to fit within max width."""
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            width = bbox[2] - bbox[0]

            if width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines if lines else [text]

    def check_ffmpeg_available(self) -> bool:
        """Check if FFmpeg is available."""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
