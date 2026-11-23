"""Device detection, model recommendations, and local setup helpers."""

from .bootstrap import install_local_runtime, pull_ollama_models
from .hardware import collect_hardware
from .models import recommend_models
from .profile import load_profile, save_profile, sync_profile_to_env
from .readiness import scan_system

__all__ = [
    "collect_hardware",
    "install_local_runtime",
    "load_profile",
    "pull_ollama_models",
    "recommend_models",
    "save_profile",
    "scan_system",
    "sync_profile_to_env",
]
