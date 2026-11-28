"""Local speech-to-text adapter and transcription interface."""

from pathlib import Path
from threading import Event
from typing import Callable, Protocol

from .cancellation import raise_if_cancelled
from .models import TranscriptSegment, TranscriptWord


class Transcriber(Protocol):
    """Port implemented by local timestamped speech-to-text providers."""

    def transcribe(
        self,
        video_path: Path,
        language: str | None = None,
        cancel_event: Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        progress_duration_seconds: float | None = None,
    ) -> tuple[TranscriptSegment, ...]:
        """Return ordered speech segments with source-video timestamps."""


class FasterWhisperTranscriber:
    """Run a preinstalled Faster-Whisper model without downloading model files."""

    def __init__(
        self,
        model_path: Path,
        device: str = "cpu",
        compute_type: str | None = None,
        beam_size: int = 5,
        status_callback: Callable[[str], None] | None = None,
    ):
        self.model_path = Path(model_path).expanduser().resolve()
        if not self.model_path.is_dir():
            raise FileNotFoundError(
                "Local transcription model folder not found: "
                f"{self.model_path}. Install the model before analyzing videos."
            )
        if device.strip().lower() not in {"auto", "cpu", "cuda"}:
            raise ValueError("Transcription device must be auto, cpu, or cuda.")
        if compute_type is not None and not compute_type.strip():
            raise ValueError("Transcription compute type cannot be empty.")
        if beam_size < 1:
            raise ValueError("Transcription beam size must be positive.")
        self.device = device.strip().lower()
        self.compute_type = compute_type.strip() if compute_type else None
        self.beam_size = beam_size
        self.status_callback = status_callback
        self._model = None

    def transcribe(
        self,
        video_path: Path,
        language: str | None = None,
        cancel_event: Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        progress_duration_seconds: float | None = None,
    ) -> tuple[TranscriptSegment, ...]:
        """Transcribe with a local model directory, refusing implicit downloads."""
        raise_if_cancelled(cancel_event)
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")
        model = self._load_model()
        raise_if_cancelled(cancel_event)
        try:
            segments, _ = model.transcribe(
                str(path),
                language=language,
                beam_size=self.beam_size,
                vad_filter=True,
                word_timestamps=True,
            )
            transcript = []
            for segment in segments:
                raise_if_cancelled(cancel_event)
                text = str(segment.text).strip()
                if text:
                    segment_start = float(segment.start)
                    segment_end = float(segment.end)
                    words = []
                    previous_word_end = segment_start
                    for word in getattr(segment, "words", None) or ():
                        word_text = str(word.word).strip()
                        if not word_text:
                            continue
                        word_start = max(
                            segment_start,
                            previous_word_end,
                            float(word.start),
                        )
                        word_end = min(segment_end, float(word.end))
                        if word_end > word_start:
                            words.append(
                                TranscriptWord(
                                    start_seconds=word_start,
                                    end_seconds=word_end,
                                    text=word_text,
                                )
                            )
                            previous_word_end = word_end
                    transcript.append(
                        TranscriptSegment(
                            start_seconds=segment_start,
                            end_seconds=segment_end,
                            text=text,
                            words=tuple(words),
                        )
                    )
                    if (
                        progress_callback is not None
                        and progress_duration_seconds is not None
                        and progress_duration_seconds > 0
                    ):
                        progress_callback(
                            min(1.0, segment_end / progress_duration_seconds)
                        )
            raise_if_cancelled(cancel_event)
            if progress_callback is not None:
                progress_callback(1.0)
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(f"Local transcription failed: {error}") from error
        return tuple(transcript)

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

        device, compute_type = self._resolve_device()
        self._emit_status(
            f"Transcription device: {device.upper()} ({compute_type})."
        )
        try:
            self._model = WhisperModel(
                str(self.model_path),
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
        except TypeError as error:
            raise RuntimeError(
                "The installed Faster-Whisper version does not support local-only "
                "model loading. Update the optional AI dependencies."
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            if self.device == "auto" and device == "cuda":
                self._emit_status(
                    f"CUDA could not initialize ({error}); retrying transcription "
                    "on the CPU."
                )
                try:
                    self._model = WhisperModel(
                        str(self.model_path),
                        device="cpu",
                        compute_type=self.compute_type or "int8",
                        local_files_only=True,
                    )
                except (OSError, RuntimeError, ValueError) as cpu_error:
                    raise RuntimeError(
                        f"CUDA initialization failed: {error}. CPU fallback "
                        f"also failed: {cpu_error}"
                    ) from cpu_error
                self._emit_status("Using CPU fallback for transcription.")
                return self._model
            raise RuntimeError(
                f"Could not load local transcription model at {self.model_path} "
                f"on {device.upper()}: {error}"
            ) from error
        return self._model

    def _resolve_device(self) -> tuple[str, str]:
        if self.device == "cpu":
            return "cpu", self.compute_type or "int8"
        try:
            import ctranslate2

            cuda_device_count = ctranslate2.get_cuda_device_count()
            compute_types = ctranslate2.get_supported_compute_types("cuda")
        except (AttributeError, ImportError, OSError, RuntimeError) as error:
            return self._cuda_unavailable(str(error))
        if cuda_device_count < 1:
            return self._cuda_unavailable(
                "CTranslate2 did not find an NVIDIA CUDA device."
            )
        if self.compute_type:
            compute_type = self.compute_type
        elif "float16" in compute_types:
            compute_type = "float16"
        elif "int8_float16" in compute_types:
            compute_type = "int8_float16"
        else:
            return self._cuda_unavailable(
                "CTranslate2 reports no supported CUDA precision."
            )
        return "cuda", compute_type

    def _cuda_unavailable(self, reason: str) -> tuple[str, str]:
        if self.device == "auto":
            self._emit_status(
                f"NVIDIA CUDA is unavailable ({reason}); using CPU (int8)."
            )
            return "cpu", self.compute_type or "int8"
        raise RuntimeError(
            "NVIDIA CUDA is unavailable to Faster-Whisper. Install compatible "
            "CUDA/cuDNN runtime libraries or select Auto/CPU. "
            f"Details: {reason}"
        )

    def _emit_status(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)
