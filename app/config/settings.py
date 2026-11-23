"""Application settings and configuration."""

import os
from pathlib import Path
from typing import List, Tuple
from dotenv import load_dotenv


class Settings:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        """Initialize settings from environment."""
        # Google Drive
        self.google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self.google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        self.google_token_file: Path = Path(os.getenv("GOOGLE_TOKEN_FILE", "data/google_token.json"))

        # YouTube
        self.youtube_client_secret_file: Path = Path(
            os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "data/youtube_client_secret.json")
        )
        self.youtube_token_file: Path = Path(os.getenv("YOUTUBE_TOKEN_FILE", "data/youtube_token.json"))
        self.youtube_privacy_status: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "private")

        # Application behavior
        self.dry_run: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

        # AI providers. Credentials are read but never exposed through status values.
        self.story_provider: str = os.getenv("STORY_PROVIDER", "mock").strip().lower()
        self.tts_provider: str = os.getenv("TTS_PROVIDER", "offline").strip().lower()
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        self.openai_story_model: str = os.getenv("OPENAI_STORY_MODEL", "gpt-4o-mini")
        self.anthropic_story_model: str = os.getenv(
            "ANTHROPIC_STORY_MODEL", "claude-sonnet-4-20250514"
        )
        self.gemini_story_model: str = os.getenv(
            "GEMINI_STORY_MODEL", "gemini-3.8-flash"
        )
        self.ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
        self.ollama_embedding_model: str = os.getenv(
            "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"
        )
        self.web_research_enabled: bool = os.getenv(
            "WEB_RESEARCH_ENABLED", "false"
        ).strip().lower() in ("true", "1", "yes")
        self.ollama_base_url: str = os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        )
        self.ollama_timeout_seconds: float = float(
            os.getenv("OLLAMA_TIMEOUT_SECONDS", "300")
        )
        self.openai_tts_model: str = os.getenv("OPENAI_TTS_MODEL", "tts-1")
        self.openai_tts_voice: str = os.getenv("OPENAI_TTS_VOICE", "alloy")

        # Administrator access & usage limits
        self.admin_key: str = os.getenv("ADMIN_KEY", "").strip()
        self.daily_video_limit: int = int(os.getenv("DAILY_VIDEO_LIMIT", "2"))

        # Directories
        self.output_dir: Path = Path(os.getenv("OUTPUT_DIR", "data/output"))
        self.input_dir: Path = Path(os.getenv("INPUT_DIR", "data/input"))
        self.temp_dir: Path = Path(os.getenv("TEMP_DIR", "data/temp"))

        # Database
        self.database_path: Path = Path(os.getenv("DATABASE_PATH", "data/automation.sqlite3"))

        # Caption configuration
        self.caption_max_length: int = int(os.getenv("CAPTION_MAX_LENGTH", "100"))
        self.caption_style: str = os.getenv("CAPTION_STYLE", "dark_humor")
        self.caption_font_size: int = int(os.getenv("CAPTION_FONT_SIZE", "48"))
        self.caption_padding: int = int(os.getenv("CAPTION_PADDING", "20"))

        # Parse RGBA colors from environment
        self.caption_bg_color: Tuple[int, int, int, int] = self._parse_rgba(
            os.getenv("CAPTION_BOG_COLOR", "0,0,0,200")
        )
        self.caption_text_color: Tuple[int, int, int, int] = self._parse_rgba(
            os.getenv("CAPTION_TEXT_COLOR", "255,255,255,255")
        )

        # Logging
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")
        self.log_file: Path = Path(os.getenv("LOG_FILE", "logs/automation.log"))

        # Processing
        self.max_file_size_mb: int = int(os.getenv("MAX_FILE_SIZE_MB", "500"))
        self.supported_image_formats: List[str] = os.getenv(
            "SUPPORTED_IMAGE_FORMATS", "jpg,jpeg,png,webp"
        ).split(",")
        self.supported_video_formats: List[str] = os.getenv(
            "SUPPORTED_VIDEO_FORMATS", "mp4,mov,webm,mkv"
        ).split(",")

        # FFmpeg. Prefer a bundled copy from the installer when PATH is unset.
        self.ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.ffprobe_path: str = os.getenv("FFPROBE_PATH", "ffprobe")
        self._apply_bundled_ffmpeg()

    def _apply_bundled_ffmpeg(self) -> None:
        """Use tools/ffmpeg when the installer placed binaries there."""
        bundled_ffmpeg = Path("tools/ffmpeg/bin/ffmpeg.exe")
        bundled_ffprobe = Path("tools/ffmpeg/bin/ffprobe.exe")
        if self.ffmpeg_path == "ffmpeg" and bundled_ffmpeg.is_file():
            self.ffmpeg_path = str(bundled_ffmpeg.resolve())
        if self.ffprobe_path == "ffprobe" and bundled_ffprobe.is_file():
            self.ffprobe_path = str(bundled_ffprobe.resolve())

    def _parse_rgba(self, color_str: str) -> Tuple[int, int, int, int]:
        """Parse RGBA color from comma-separated string."""
        try:
            parts = [int(x.strip()) for x in color_str.split(",")]
            if len(parts) == 4:
                return tuple(parts)
        except (ValueError, AttributeError):
            pass
        return (0, 0, 0, 200)

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # Check required directories exist or can be created
        for dir_path in [self.output_dir, self.input_dir, self.temp_dir, self.log_file.parent]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create directory {dir_path}: {e}")

        # Validate caption settings
        if self.caption_max_length < 1:
            errors.append("CAPTION_MAX_LENGTH must be positive")

        if self.caption_font_size < 1:
            errors.append("CAPTION_FONT_SIZE must be positive")

        if self.max_file_size_mb < 1:
            errors.append("MAX_FILE_SIZE_MB must be positive")

        # Validate privacy status
        valid_privacy = ["private", "unlisted", "public"]
        if self.youtube_privacy_status not in valid_privacy:
            errors.append(f"YOUTUBE_PRIVACY_STATUS must be one of: {', '.join(valid_privacy)}")

        return errors

    def is_google_configured(self) -> bool:
        """Check if Google Drive credentials are configured."""
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_drive_folder_id
        )

    def is_youtube_configured(self) -> bool:
        """Check if YouTube credentials are configured."""
        return self.youtube_client_secret_file.exists()

    def ai_provider_status(self) -> dict:
        """Return configured/not configured state without exposing credentials."""
        story_keys = {
            "mock": True,
            "template": True,
            "openai": bool(self.openai_api_key),
            "anthropic": bool(self.anthropic_api_key),
            "gemini": bool(self.gemini_api_key),
            "ollama": bool(self.ollama_base_url.strip() and self.ollama_model.strip()),
        }
        tts_keys = {
            "mock": True,
            "offline": True,
            "openai": bool(self.openai_api_key),
            "elevenlabs": False,
        }
        return {
            "story": "configured" if story_keys.get(self.story_provider, False) else "not configured",
            "tts": "configured" if tts_keys.get(self.tts_provider, False) else "not configured",
        }

    def is_admin_mode(self) -> bool:
        """Check if Administrator Mode is active based on the admin key."""
        from ..limits import verify_admin_key
        return verify_admin_key(self.admin_key)


def load_settings(env_file: str = ".env") -> Settings:
    """Load settings from environment file."""
    load_dotenv(env_file, override=True)
    return Settings()
