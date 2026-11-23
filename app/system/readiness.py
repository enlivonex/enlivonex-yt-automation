"""Scan whether this PC can run the local automation stack."""

from __future__ import annotations

import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.ai.ollama import OllamaClient
from app.utils.ffmpeg import FFmpegWrapper

from .hardware import HardwareInfo, collect_hardware
from .models import (
    DEFAULT_STORY_MODEL,
    EMBEDDING_MODEL,
    ModelRecommendation,
    recommend_models,
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemScan:
    hardware: HardwareInfo
    recommendation: ModelRecommendation
    checks: list[CheckResult] = field(default_factory=list)
    installed_ollama_models: list[str] = field(default_factory=list)
    ready: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hardware": self.hardware.to_dict(),
            "recommendation": {
                "story_model": self.recommendation.story_model,
                "embedding_model": self.recommendation.embedding_model,
                "compatible_story_models": self.recommendation.compatible_story_models,
                "reason": self.recommendation.reason,
                "can_run_default": self.recommendation.can_run_default,
            },
            "checks": [check.to_dict() for check in self.checks],
            "installed_ollama_models": self.installed_ollama_models,
            "ready": self.ready,
            "summary": self.summary,
        }


ProgressCallback = Callable[[str], None]


def scan_system(
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    ollama_base_url: str = "http://127.0.0.1:11434",
    story_model: str = DEFAULT_STORY_MODEL,
    embedding_model: str = EMBEDDING_MODEL.name,
    root: str | Path = ".",
    progress: Optional[ProgressCallback] = None,
) -> SystemScan:
    """Inspect hardware, FFmpeg, TTS, and Ollama models on this PC."""
    _emit(progress, "Detecting this PC...")
    hardware = collect_hardware(root)
    installed_models: list[str] = []
    ollama_detail = "Ollama was not reached."
    ollama_ok = False
    try:
        _emit(progress, "Connecting to Ollama...")
        client = OllamaClient(base_url=ollama_base_url, model=story_model)
        installed_models = client.list_installed_models()
        ollama_ok = True
        ollama_detail = (
            f"Ollama is running with {len(installed_models)} local model(s)."
            if installed_models
            else "Ollama is running, but no models are installed yet."
        )
    except RuntimeError as error:
        ollama_detail = str(error)

    recommendation = recommend_models(hardware, installed_models)
    ffmpeg = FFmpegWrapper(ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
    checks = [
        CheckResult(
            "Python 3.11+",
            sys.version_info >= (3, 11),
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        CheckResult(
            "Memory",
            hardware.ram_gb >= 4,
            f"{hardware.ram_gb:g} GB RAM",
        ),
        CheckResult(
            "Disk space",
            hardware.disk_free_gb >= 8,
            f"{hardware.disk_free_gb:g} GB free (8 GB recommended for models and video jobs)",
        ),
        CheckResult(
            "FFmpeg",
            ffmpeg.is_available(),
            "Found" if ffmpeg.is_available() else "Missing; video rendering will fail until installed",
        ),
        CheckResult(
            "Offline TTS (Windows voices)",
            _tts_available(),
            "pyttsx3 and system voices are available"
            if _tts_available()
            else "Install pyttsx3; Windows SAPI voices are used locally",
        ),
        CheckResult(
            "Ollama",
            ollama_ok,
            ollama_detail,
        ),
        CheckResult(
            "Story model",
            _model_installed(story_model, installed_models),
            _model_detail(story_model, installed_models, recommendation.story_model),
            blocking=False,
        ),
        CheckResult(
            "RAG embedding model",
            _model_installed(embedding_model, installed_models),
            _model_detail(embedding_model, installed_models, embedding_model),
            blocking=False,
        ),
        CheckResult(
            "Default Qwen 2.5 3B",
            recommendation.can_run_default,
            "This PC can run qwen2.5:3b"
            if recommendation.can_run_default
            else "This PC is below 8 GB RAM; choose a smaller compatible model",
            blocking=False,
        ),
    ]
    blocking_failed = [check for check in checks if check.blocking and not check.ok]
    ready = not blocking_failed
    if ready:
        summary = "This PC can run Enlivonex locally. Save the selected models once to keep using them."
    else:
        summary = "This PC is missing required components. Use Install missing components, then scan again."
    _emit(progress, "Scan complete.")
    return SystemScan(
        hardware=hardware,
        recommendation=recommendation,
        checks=checks,
        installed_ollama_models=installed_models,
        ready=ready,
        summary=summary,
    )


def format_scan(scan: SystemScan) -> str:
    hardware = scan.hardware
    lines = [
        f"PC: {hardware.hostname}",
        f"OS: {hardware.os_name} {hardware.os_release} ({hardware.architecture})",
        f"CPU: {hardware.cpu_name} ({hardware.cpu_cores} cores)",
        f"RAM: {hardware.ram_gb:g} GB",
        f"Free disk: {hardware.disk_free_gb:g} GB",
        f"GPU: {hardware.gpu_name}"
        + (f" ({hardware.vram_gb:g} GB VRAM)" if hardware.vram_gb else ""),
        "",
        scan.recommendation.reason,
        f"Compatible story models: {', '.join(scan.recommendation.compatible_story_models)}",
        "",
    ]
    for check in scan.checks:
        mark = "OK" if check.ok else "MISSING"
        lines.append(f"[{mark}] {check.name}: {check.detail}")
    lines.extend(["", scan.summary])
    return "\n".join(lines)


def _model_installed(name: str, installed: list[str]) -> bool:
    names = set(installed)
    return name in names or any(item.split(":")[0] == name.split(":")[0] and item == name for item in installed)


def _model_detail(name: str, installed: list[str], recommended: str) -> str:
    if _model_installed(name, installed):
        return f"{name} is installed"
    return f"{name} is not installed. Recommended for this PC: {recommended}"


def _tts_available() -> bool:
    try:
        import pyttsx3  # noqa: F401

        return True
    except Exception:
        return sys.platform.startswith("win")


def _emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress:
        progress(message)
