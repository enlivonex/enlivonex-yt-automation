"""Persist one-time PC and model settings for this installation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import set_key

from .hardware import HardwareInfo, collect_hardware

PROFILE_PATH = Path("data/device_profile.json")


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_profile(
    *,
    hardware: HardwareInfo | None = None,
    story_provider: str,
    ollama_model: str,
    ollama_embedding_model: str,
    ollama_base_url: str,
    tts_provider: str,
    path: Path = PROFILE_PATH,
) -> dict[str, Any]:
    snapshot = hardware or collect_hardware()
    payload = {
        "version": 1,
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "machine_id": snapshot.machine_id,
        "hostname": snapshot.hostname,
        "hardware": snapshot.to_dict(),
        "story_provider": story_provider,
        "ollama_model": ollama_model,
        "ollama_embedding_model": ollama_embedding_model,
        "ollama_base_url": ollama_base_url,
        "tts_provider": tts_provider,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def sync_profile_to_env(
    env_file: Path,
    *,
    story_provider: str,
    ollama_model: str,
    ollama_embedding_model: str,
    ollama_base_url: str,
    tts_provider: str,
) -> None:
    if not env_file.exists():
        env_file.touch()
    set_key(str(env_file), "STORY_PROVIDER", story_provider)
    set_key(str(env_file), "OLLAMA_MODEL", ollama_model)
    set_key(str(env_file), "OLLAMA_EMBEDDING_MODEL", ollama_embedding_model)
    set_key(str(env_file), "OLLAMA_BASE_URL", ollama_base_url)
    set_key(str(env_file), "TTS_PROVIDER", tts_provider)


def profile_matches_this_pc(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    return profile.get("machine_id") == collect_hardware().machine_id
