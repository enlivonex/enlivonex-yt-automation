"""Video composition for YouTube Shorts."""

from .composer import ShortsComposer, CompositionConfig
from .duration import DurationManager, DurationPlan

__all__ = ["ShortsComposer", "CompositionConfig", "DurationManager", "DurationPlan"]
