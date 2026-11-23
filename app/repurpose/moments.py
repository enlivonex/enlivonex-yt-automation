"""Local-model ranking of transcript-derived candidate moments."""

import json
import math
from typing import Any, Protocol

from ..ai.ollama import JSONGenerator
from .models import MomentCandidate, TranscriptSegment

MAX_RANKING_WINDOWS = 32


class MomentRanker(Protocol):
    """Port for ranking candidate moments with a local model."""

    def rank(
        self,
        transcript: tuple[TranscriptSegment, ...],
        source_duration: float,
        min_duration: float,
        max_duration: float,
        max_candidates: int = 8,
    ) -> tuple[MomentCandidate, ...]:
        """Return ranked, duration-bounded candidate ranges."""


class OllamaMomentRanker:
    """Generate transcript windows and ask an existing local Ollama model to rank."""

    def __init__(self, client: JSONGenerator):
        self.client = client

    def rank(
        self,
        transcript: tuple[TranscriptSegment, ...],
        source_duration: float,
        min_duration: float,
        max_duration: float,
        max_candidates: int = 8,
    ) -> tuple[MomentCandidate, ...]:
        _validate_options(source_duration, min_duration, max_duration, max_candidates)
        windows = _build_windows(
            transcript, source_duration, min_duration, max_duration
        )
        windows = _sample_windows(windows, MAX_RANKING_WINDOWS)
        if not windows:
            return ()

        response = self.client.generate_json(
            "Rank candidate short-video moments using only the supplied transcript. "
            "Prefer a self-contained thought with an engaging hook, clear context, "
            "and satisfying ending. Return JSON with a 'candidates' array. Each "
            "entry must contain integer 'id', numeric 'score' from 0 to 1, and a "
            "short 'rationale'. Do not change timestamps or invent transcript.",
            json.dumps(
                {
                    "max_results": max_candidates,
                    "candidates": [
                        {
                            "id": index,
                            "start_seconds": start,
                            "end_seconds": end,
                            "transcript": text,
                        }
                        for index, (start, end, text) in enumerate(windows)
                    ],
                },
                ensure_ascii=False,
            ),
        )
        ranked = _parse_ranked_candidates(
            response, windows, source_duration, min_duration, max_duration
        )
        return tuple(ranked[:max_candidates])


def _validate_options(
    source_duration: float,
    min_duration: float,
    max_duration: float,
    max_candidates: int,
) -> None:
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise ValueError("Source duration must be finite and positive.")
    if not math.isfinite(min_duration) or min_duration <= 0:
        raise ValueError("Minimum moment duration must be finite and positive.")
    if not math.isfinite(max_duration) or max_duration < min_duration:
        raise ValueError("Maximum duration must be finite and at least the minimum.")
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or max_candidates < 1
    ):
        raise ValueError("Maximum candidate count must be positive.")


def _build_windows(
    transcript: tuple[TranscriptSegment, ...],
    source_duration: float,
    min_duration: float,
    max_duration: float,
) -> list[tuple[float, float, str]]:
    max_duration = min(max_duration, source_duration)
    if min_duration > max_duration:
        return []

    validated = []
    previous_end = 0.0
    for segment in transcript:
        if segment.start_seconds < previous_end:
            raise ValueError("Transcript segments must be ordered and non-overlapping.")
        if segment.end_seconds > source_duration:
            raise ValueError("Transcript segment exceeds source duration.")
        previous_end = segment.end_seconds
        validated.append(segment)

    windows = []
    index = 0
    target_duration = (min_duration + max_duration) / 2
    stride = max(min_duration, target_duration / 2)
    next_start = 0.0
    while index < len(validated):
        while (
            index < len(validated)
            and validated[index].start_seconds < next_start
        ):
            index += 1
        if index >= len(validated):
            break

        start_index = index
        start = validated[start_index].start_seconds
        end_index = start_index
        while (
            end_index + 1 < len(validated)
            and validated[end_index + 1].end_seconds - start <= target_duration
        ):
            end_index += 1

        end = validated[end_index].end_seconds
        duration = end - start
        if min_duration <= duration <= max_duration:
            text = " ".join(
                segment.text for segment in validated[start_index:end_index + 1]
            ).strip()
            if text:
                windows.append((start, end, text))
        next_start = start + stride
        index = start_index + 1
    return windows


def _sample_windows(
    windows: list[tuple[float, float, str]], limit: int
) -> list[tuple[float, float, str]]:
    if limit < 1:
        raise ValueError("Window sampling limit must be positive.")
    if len(windows) <= limit:
        return windows
    if limit == 1:
        return [windows[len(windows) // 2]]
    last_index = len(windows) - 1
    indexes = {
        round(index * last_index / (limit - 1))
        for index in range(limit)
    }
    return [windows[index] for index in sorted(indexes)]


def _parse_ranked_candidates(
    response: dict[str, Any],
    windows: list[tuple[float, float, str]],
    source_duration: float,
    min_duration: float,
    max_duration: float,
) -> list[MomentCandidate]:
    items = response.get("candidates")
    if not isinstance(items, list):
        raise RuntimeError("Local moment model returned no candidates array.")

    parsed = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Local moment model returned an invalid candidate.")
        candidate_id = item.get("id")
        score = item.get("score")
        rationale = item.get("rationale")
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 0
            or candidate_id >= len(windows)
            or candidate_id in seen
        ):
            raise RuntimeError("Local moment model returned an invalid candidate id.")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise RuntimeError("Local moment model returned an invalid candidate score.")
        if not isinstance(rationale, str) or not rationale.strip():
            raise RuntimeError("Local moment model returned no candidate rationale.")

        start, end, text = windows[candidate_id]
        duration = end - start
        if (
            end > source_duration
            or duration < min_duration
            or duration > max_duration
        ):
            raise RuntimeError("Candidate range is outside the configured duration.")
        parsed.append(
            MomentCandidate(start, end, text, float(score), rationale.strip())
        )
        seen.add(candidate_id)

    return sorted(parsed, key=lambda item: (-item.score, item.start_seconds))
