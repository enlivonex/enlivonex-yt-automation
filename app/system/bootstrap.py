"""Install FFmpeg, Ollama, Python packages, and local models on this PC."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.request import urlretrieve

from app.ai.ollama import OllamaClient
from app.utils.process import hidden_process_creation_flags

from .readiness import scan_system

ProgressCallback = Callable[[str], None]

FFMPEG_WINDOWS_ZIP = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl.zip"
)
OLLAMA_WINDOWS_SETUP = "https://ollama.com/download/OllamaSetup.exe"


def install_local_runtime(
    *,
    tools_dir: Path = Path("tools"),
    ollama_base_url: str = "http://127.0.0.1:11434",
    story_model: str = "qwen2.5:3b",
    embedding_model: str = "nomic-embed-text",
    pull_models: bool = True,
    progress: Optional[ProgressCallback] = None,
) -> list[str]:
    """Install missing local runtimes without changing existing app logic."""
    notes: list[str] = []
    _emit(progress, "Installing Python packages...")
    notes.extend(_install_python_packages(progress))
    _emit(progress, "Installing FFmpeg...")
    notes.extend(_ensure_ffmpeg(tools_dir, progress))
    _emit(progress, "Installing Ollama...")
    notes.extend(_ensure_ollama(tools_dir, progress))
    if pull_models:
        _emit(progress, "Downloading local AI models...")
        notes.extend(
            pull_ollama_models(
                [story_model, embedding_model],
                base_url=ollama_base_url,
                progress=progress,
            )
        )
    scan = scan_system(
        ffmpeg_path=_bundled_ffmpeg(tools_dir) or "ffmpeg",
        ffprobe_path=_bundled_ffprobe(tools_dir) or "ffprobe",
        ollama_base_url=ollama_base_url,
        story_model=story_model,
        embedding_model=embedding_model,
        progress=progress,
    )
    if scan.ready:
        notes.append("Local runtime is ready on this PC.")
    else:
        missing = [check.name for check in scan.checks if check.blocking and not check.ok]
        notes.append("Still missing: " + ", ".join(missing) if missing else scan.summary)
    return notes


def pull_ollama_models(
    models: Iterable[str],
    *,
    base_url: str = "http://127.0.0.1:11434",
    progress: Optional[ProgressCallback] = None,
) -> list[str]:
    notes: list[str] = []
    unique = list(dict.fromkeys([name for name in models if name]))
    client = OllamaClient(base_url=base_url, model=unique[0] if unique else "qwen2.5:3b")
    try:
        installed = set(client.list_installed_models())
    except RuntimeError as error:
        return [f"Could not reach Ollama to download models: {error}"]
    for name in unique:
        if name in installed:
            notes.append(f"Ollama model already present: {name}")
            continue
        _emit(progress, f"Downloading {name}...")
        try:
            client.pull_model(name)
            notes.append(f"Downloaded Ollama model: {name}")
        except RuntimeError as error:
            notes.append(str(error))
    return notes


def bundled_ffmpeg_paths(tools_dir: Path = Path("tools")) -> tuple[str, str] | None:
    ffmpeg = _bundled_ffmpeg(tools_dir)
    ffprobe = _bundled_ffprobe(tools_dir)
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    return None


def _install_python_packages(progress: Optional[ProgressCallback]) -> list[str]:
    # PyInstaller distributions carry their Python runtime and dependencies.
    # Never try to mutate the user's Python installation from the packaged app.
    if getattr(sys, "frozen", False):
        return ["Python runtime and app dependencies are bundled with this build."]
    root = Path(__file__).resolve().parents[2]
    requirements = [
        path
        for path in (root / "requirements.txt", root / "requirements-ai.txt")
        if path.exists()
    ]
    if not requirements:
        return ["No requirements files found."]
    command = [sys.executable, "-m", "pip", "install", "-q"]
    for path in requirements:
        command.extend(["-r", str(path)])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            creationflags=hidden_process_creation_flags(),
        )
    except OSError as error:
        return [f"Could not install Python packages: {error}"]
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "pip failed").strip()
        return [f"Python package install failed: {detail[:400]}"]
    _emit(progress, "Python packages are installed.")
    return ["Python packages installed (including offline TTS)."]


def _ensure_ffmpeg(tools_dir: Path, progress: Optional[ProgressCallback]) -> list[str]:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return ["FFmpeg is already on PATH."]
    bundled = bundled_ffmpeg_paths(tools_dir)
    if bundled:
        return [f"Using bundled FFmpeg at {bundled[0]}"]
    if os.name != "nt":
        return ["Install FFmpeg with your system package manager, then scan again."]
    if _winget_install("Gyan.FFmpeg", progress):
        return ["FFmpeg installed with winget."]
    try:
        _emit(progress, "Downloading FFmpeg...")
        tools_dir.mkdir(parents=True, exist_ok=True)
        archive = tools_dir / "ffmpeg.zip"
        urlretrieve(FFMPEG_WINDOWS_ZIP, archive)
        extract_dir = tools_dir / "ffmpeg-extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(extract_dir)
        bin_dir = next(extract_dir.rglob("ffmpeg.exe")).parent
        target = tools_dir / "ffmpeg"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(bin_dir.parent, target)
        archive.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)
        return [f"FFmpeg installed to {target}"]
    except Exception as error:
        return [f"Could not install FFmpeg automatically: {error}"]


def _ensure_ollama(tools_dir: Path, progress: Optional[ProgressCallback]) -> list[str]:
    if OllamaClient._find_ollama_executable():
        return ["Ollama is already installed."]
    if os.name != "nt":
        return ["Install Ollama from https://ollama.com/download then scan again."]
    if _winget_install("Ollama.Ollama", progress):
        return ["Ollama installed with winget."]
    setup = tools_dir / "OllamaSetup.exe"
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        _emit(progress, "Downloading Ollama installer...")
        urlretrieve(OLLAMA_WINDOWS_SETUP, setup)
        _emit(progress, "Running Ollama installer...")
        completed = subprocess.run(
            [str(setup), "/VERYSILENT", "/NORESTART"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=hidden_process_creation_flags(),
        )
        if completed.returncode != 0:
            return [
                "Ollama installer did not finish silently. "
                "Launch OllamaSetup.exe from the tools folder and complete setup."
            ]
        return ["Ollama installer completed."]
    except Exception as error:
        return [f"Could not install Ollama automatically: {error}"]


def _winget_install(package_id: str, progress: Optional[ProgressCallback]) -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False
    _emit(progress, f"Installing {package_id} with winget...")
    completed = subprocess.run(
        [
            winget,
            "install",
            "-e",
            "--id",
            package_id,
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_creation_flags(),
    )
    return completed.returncode == 0


def _bundled_ffmpeg(tools_dir: Path) -> Optional[str]:
    candidates = [
        tools_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
        tools_dir / "ffmpeg" / "ffmpeg.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    return None


def _bundled_ffprobe(tools_dir: Path) -> Optional[str]:
    candidates = [
        tools_dir / "ffmpeg" / "bin" / "ffprobe.exe",
        tools_dir / "ffmpeg" / "ffprobe.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    return None


def _emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress:
        progress(message)
