"""Clip selection strategies for choosing source videos."""

from typing import List, Optional
from abc import ABC, abstractmethod
import random

from ..database.models import SourceClip


class ClipSelectionError(RuntimeError):
    """Raised when clip selection fails, with diagnostic context.

    The error message explains which selector rejected which clips and why,
    so the user can understand whether the problem is missing data or a
    misconfigured filter.
    """
    pass


class ClipSelector(ABC):
    """Base class for clip selection strategies."""

    @abstractmethod
    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Select clips from available pool.

        Args:
            clips: List of available clips
            count: Number of clips to select

        Returns:
            Selected clips (may raise ClipSelectionError if constraints cannot be met)
        """
        pass


class RandomSelector(ClipSelector):
    """Randomly select clips."""

    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Randomly select clips."""
        if not clips:
            return []

        return random.sample(clips, min(count, len(clips)))


class LeastUsedSelector(ClipSelector):
    """Select least-used clips first."""

    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Select clips with lowest usage count."""
        if not clips:
            return []

        # Sort by times_used (ascending), then by created_at (oldest first)
        sorted_clips = sorted(
            clips,
            key=lambda c: (c.times_used, c.created_at)
        )

        return sorted_clips[:count]


class OrientationSelector(ClipSelector):
    """Select clips by orientation preference.

    This is a PREFERENCE selector, not a hard filter. Preferred clips are
    returned first, followed by other orientations as a fallback.
    """

    def __init__(self, preferred_orientation: str = "portrait"):
        """Initialize with preferred orientation.

        Args:
            preferred_orientation: portrait, landscape, or square
        """
        self.preferred_orientation = preferred_orientation

    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Select clips matching orientation preference (with fallback)."""
        if not clips:
            return []

        preferred = [c for c in clips if c.orientation == self.preferred_orientation]
        fallback = [c for c in clips if c.orientation != self.preferred_orientation]
        selector = LeastUsedSelector()
        ranked = selector.select(preferred, len(preferred)) + selector.select(
            fallback, len(fallback)
        )
        return ranked[:count]


class DurationSelector(ClipSelector):
    """Prefer clips in a duration range without rejecting available clips."""

    def __init__(self, min_duration: float = 0, max_duration: float = float('inf')):
        """Initialize with duration constraints.

        Args:
            min_duration: Minimum duration in seconds (inclusive)
            max_duration: Maximum duration in seconds (inclusive)
        """
        self.min_duration = min_duration
        self.max_duration = max_duration

    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Return preferred-duration clips first, then fallback clips."""
        if not clips:
            return []

        preferred = [
            c for c in clips
            if self.min_duration <= c.duration_seconds <= self.max_duration
        ]
        fallback = [c for c in clips if c not in preferred]

        selector = LeastUsedSelector()
        ranked = selector.select(preferred, len(preferred)) + selector.select(
            fallback, len(fallback)
        )
        return ranked[:count]


class CompositeSelector(ClipSelector):
    """Combine orientation/duration preferences and final selection ranking."""

    def __init__(self, selectors: List[ClipSelector]):
        """Initialize with list of selectors to apply in sequence.

        Args:
            selectors: List of selectors (applied in order; last one picks final)
        """
        self.selectors = selectors

    def select(self, clips: List[SourceClip], count: int = 1) -> List[SourceClip]:
        """Rank preferred clips first and preserve least-used selection."""
        if not clips or not self.selectors:
            return []

        preferences = [
            selector for selector in self.selectors
            if isinstance(selector, (OrientationSelector, DurationSelector))
        ]
        other_selectors = [
            selector for selector in self.selectors
            if not isinstance(selector, (OrientationSelector, DurationSelector))
        ]

        if not other_selectors or (
            len(other_selectors) == 1
            and isinstance(other_selectors[0], LeastUsedSelector)
        ):
            ranked = sorted(
                clips,
                key=lambda clip: (
                    *(
                        0 if self._matches_preference(selector, clip) else 1
                        for selector in preferences
                    ),
                    clip.times_used,
                    clip.created_at,
                ),
            )
            return ranked[:count]

        pool = clips

        # Preserve support for other selector combinations that act as filters.
        for selector in self.selectors[:-1]:
            pool = selector.select(pool, len(pool))
            if not pool:
                raise ClipSelectionError(
                    f"{selector.__class__.__name__} eliminated all candidates "
                    f"(pool started with {len(clips)} clip(s)). "
                    f"Check that your library contains clips matching all filters."
                )

        return self.selectors[-1].select(pool, count)

    @staticmethod
    def _matches_preference(selector: ClipSelector, clip: SourceClip) -> bool:
        if isinstance(selector, OrientationSelector):
            return clip.orientation == selector.preferred_orientation
        if isinstance(selector, DurationSelector):
            return selector.min_duration <= clip.duration_seconds <= selector.max_duration
        return True
