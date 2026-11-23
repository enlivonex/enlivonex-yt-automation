"""Recommend local Ollama models from detected hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .hardware import HardwareInfo


@dataclass(frozen=True)
class ModelSpec:
    name: str
    min_ram_gb: float
    min_vram_gb: float
    quality: int
    role: str
    notes: str


STORY_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "qwen2.5:0.5b",
        min_ram_gb=4,
        min_vram_gb=0,
        quality=1,
        role="story",
        notes="Lowest RAM option for very small PCs.",
    ),
    ModelSpec(
        "qwen2.5:1.5b",
        min_ram_gb=6,
        min_vram_gb=0,
        quality=2,
        role="story",
        notes="Fits 8 GB class laptops with headroom for video work.",
    ),
    ModelSpec(
        "qwen2.5:3b",
        min_ram_gb=8,
        min_vram_gb=0,
        quality=3,
        role="story",
        notes="Default local story and RAG model for this app.",
    ),
    ModelSpec(
        "qwen2.5:7b",
        min_ram_gb=16,
        min_vram_gb=6,
        quality=4,
        role="story",
        notes="Better quality when 16 GB RAM or 6 GB VRAM is available.",
    ),
    ModelSpec(
        "qwen2.5:14b",
        min_ram_gb=32,
        min_vram_gb=12,
        quality=5,
        role="story",
        notes="High quality; needs a workstation-class machine.",
    ),
)

EMBEDDING_MODEL = ModelSpec(
    "nomic-embed-text",
    min_ram_gb=4,
    min_vram_gb=0,
    quality=3,
    role="embedding",
    notes="Required for local RAG document search.",
)

DEFAULT_STORY_MODEL = "qwen2.5:3b"


@dataclass
class ModelRecommendation:
    story_model: str
    embedding_model: str
    compatible_story_models: list[str]
    reason: str
    can_run_default: bool


def recommend_models(
    hardware: HardwareInfo,
    installed_models: Sequence[str] | None = None,
) -> ModelRecommendation:
    """Pick the strongest Qwen-family model this PC can run."""
    installed = _normalize_names(installed_models or [])
    compatible = [spec for spec in STORY_MODELS if _fits(spec, hardware)]
    names = [spec.name for spec in compatible]
    default_ok = any(spec.name == DEFAULT_STORY_MODEL for spec in compatible)
    chosen = _choose_story_model(compatible, installed)
    if not chosen:
        chosen = STORY_MODELS[0].name
        reason = (
            f"This PC reports {hardware.ram_gb:g} GB RAM, which is below the "
            f"recommended 8 GB for {DEFAULT_STORY_MODEL}. Use {chosen} only."
        )
    else:
        reason = (
            f"Detected {hardware.ram_gb:g} GB RAM"
            + (f" and {hardware.gpu_name}" if hardware.gpu_name else "")
            + f". Recommended story model: {chosen}."
        )
        if chosen in installed:
            reason += " It is already installed in Ollama."
    return ModelRecommendation(
        story_model=chosen,
        embedding_model=EMBEDDING_MODEL.name,
        compatible_story_models=names or [chosen],
        reason=reason,
        can_run_default=default_ok,
    )


def required_models(story_model: str) -> list[str]:
    models = [story_model]
    if EMBEDDING_MODEL.name not in models:
        models.append(EMBEDDING_MODEL.name)
    return models


def _choose_story_model(
    compatible: Sequence[ModelSpec],
    installed: set[str],
) -> str:
    if not compatible:
        return ""
    installed_matches = [spec for spec in compatible if spec.name in installed]
    pool = installed_matches or list(compatible)
    return max(pool, key=lambda spec: spec.quality).name


def _fits(spec: ModelSpec, hardware: HardwareInfo) -> bool:
    ram = hardware.ram_gb
    vram = hardware.vram_gb
    if spec.min_vram_gb > 0 and vram > 0:
        return ram >= spec.min_ram_gb or vram >= spec.min_vram_gb
    if spec.min_vram_gb > 0 and vram <= 0:
        return ram >= spec.min_ram_gb + spec.min_vram_gb
    return ram >= spec.min_ram_gb


def _normalize_names(names: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for name in names:
        if not name:
            continue
        normalized.add(name)
        if ":" in name:
            normalized.add(name.split(":", 1)[0])
    return normalized
