"""Local speech-to-text adapter and transcription interface."""

from pathlib import Path
from typing import Protocol

from .models import TranscriptSegment


class Transcriber(Protocol):
    """Port implemented by local timestamped speech-to-text providers."""

    def transcribe(
        self, video_path: Path, language: str | None = None
    ) -> tuple[TranscriptSegment, ...]:
        """Return ordered speech segments with source-video timestamps."""


class FasterWhisperTranscriber:
    """Run a preinstalled Faster-Whisper model without downloading model files."""

    def __init__(
        self,
        model_path: Path,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
    ):
        self.model_path = Path(model_path).expanduser().resolve()
        if not self.model_path.is_dir():
            raise FileNotFoundError(
                "Local transcription model folder not found: "
                f"{self.model_path}. Install the model before analyzing videos."
            )
        if not device.strip():
            raise ValueError("Transcription device cannot be empty.")
        if not compute_type.strip():
            raise ValueError("Transcription compute type cannot be empty.")
        if beam_size < 1:
            raise ValueError("Transcription beam size must be positive.")
        self.device = device.strip()
        self.compute_type = compute_type.strip()
        self.beam_size = beam_size
        self._model = None

    def transcribe(
        self, video_path: Path, language: str | None = None
    ) -> tuple[TranscriptSegment, ...]:
        """Transcribe with a local model directory, refusing implicit downloads."""
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")
        model = self._load_model()
        try:
            segments, _ = model.transcribe(
                str(path),
                language=language,
                beam_size=self.beam_size,
                vad_filter=True,
            )
            transcript = tuple(
                TranscriptSegment(
                    start_seconds=float(segment.start),
                    end_seconds=float(segment.end),
                    text=str(segment.text).strip(),
                )
                for segment in segments
                if str(segment.text).strip()
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(f"Local transcription failed: {error}") from error
        return transcript

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "Local transcription requires Faster-Whisper. Install the "
                "optional AI dependencies with `pip install -r "
                "requirements-ai.txt`."
            ) from error

        try:
            self._model = WhisperModel(
                str(self.model_path),
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
        except TypeError as error:
            raise RuntimeError(
                "The installed Faster-Whisper version does not support local-only "
                "model loading. Update the optional AI dependencies."
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                f"Could not load local transcription model at {self.model_path}: "
                f"{error}"
            ) from error
        return self._model
