"""Test for clip selection with various constraints."""

import pytest
from pathlib import Path
from datetime import datetime
from app.clips.selector import (
    CompositeSelector, OrientationSelector, DurationSelector, LeastUsedSelector
)
from app.database.models import SourceClip, ClipStatus


def test_duration_preference_ranks_matching_clips_before_fallbacks():
    """Out-of-range clips remain selectable after in-range clips."""
    clips = [
        SourceClip(
            id=1, filename="short.mp4", file_hash="hash1",
            file_path="/clips/short.mp4", duration_seconds=3.0,  # Too short
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="perfect.mp4", file_hash="hash2",
            file_path="/clips/perfect.mp4", duration_seconds=30.0,  # Perfect
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=3, filename="long.mp4", file_hash="hash3",
            file_path="/clips/long.mp4", duration_seconds=700.0,  # Too long
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selector = DurationSelector(min_duration=10.0, max_duration=60.0)
    selected = selector.select(clips, count=10)

    assert len(selected) == 3
    assert selected[0].filename == "perfect.mp4"
    assert {clip.filename for clip in selected} == {
        "short.mp4", "perfect.mp4", "long.mp4"
    }


def test_duration_preference_falls_back_to_least_used_clip_when_no_match():
    clips = [
        SourceClip(
            id=1, filename="long_used.mp4", file_hash="hash1",
            file_path="/clips/long_used.mp4", duration_seconds=427.0,
            width=854, height=480, aspect_ratio="427:240",
            orientation="landscape", file_size_bytes=50000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=4, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="long_unused.mp4", file_hash="hash2",
            file_path="/clips/long_unused.mp4", duration_seconds=700.0,
            width=854, height=480, aspect_ratio="427:240",
            orientation="landscape", file_size_bytes=50000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selected = DurationSelector(5.0, 60.0).select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].filename == "long_unused.mp4"


def test_duration_preference_ranks_least_used_within_preferred_tier():
    clips = [
        SourceClip(
            id=1, filename="preferred_used.mp4", file_hash="hash1",
            file_path="/clips/preferred_used.mp4", duration_seconds=12.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=5, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="preferred_unused.mp4", file_hash="hash2",
            file_path="/clips/preferred_unused.mp4", duration_seconds=45.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=1, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=3, filename="fallback_unused.mp4", file_hash="hash3",
            file_path="/clips/fallback_unused.mp4", duration_seconds=427.0,
            width=854, height=480, aspect_ratio="427:240",
            orientation="landscape", file_size_bytes=50000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selected = DurationSelector(5.0, 60.0).select(clips, count=3)

    assert [clip.filename for clip in selected] == [
        "preferred_unused.mp4", "preferred_used.mp4", "fallback_unused.mp4"
    ]


def test_selector_accepts_long_clips_with_permissive_defaults():
    """Regression test: ensure long clips aren't rejected by default.

    This was the root cause of the 'Clip selection failed' bug where a 427-second
    clip was rejected by the default max_duration=60.0 constraint.
    """
    clips = [
        SourceClip(
            id=1, filename="long_clip.mp4", file_hash="hash1",
            file_path="/clips/long.mp4", duration_seconds=427.0,  # 7+ minutes
            width=854, height=480, aspect_ratio="427:240",
            orientation="landscape", file_size_bytes=50000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    # New permissive defaults should accept clips up to 600s (10 minutes)
    permissive_selector = DurationSelector(min_duration=5.0, max_duration=600.0)
    selected = permissive_selector.select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].filename == "long_clip.mp4"


def test_composite_selector_without_orientation_filter():
    """Regression test: ensure clips work without orientation filtering.

    The bug occurred because the CLI defaulted to orientation='portrait',
    filtering out landscape clips even when orientation wasn't specified by user.
    """
    clips = [
        SourceClip(
            id=1, filename="landscape.mp4", file_hash="hash1",
            file_path="/clips/landscape.mp4", duration_seconds=30.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    # Selector without orientation filter should accept any orientation
    selector = CompositeSelector([
        DurationSelector(min_duration=5.0, max_duration=600.0),
        LeastUsedSelector()
    ])

    selected = selector.select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].orientation == "landscape"


def test_orientation_selector_prefers_requested_orientation_with_fallback():
    """Every orientation remains eligible when no preferred clip is available."""
    clips = [
        SourceClip(
            id=1, filename="landscape.mp4", file_hash="hash1",
            file_path="/clips/landscape.mp4", duration_seconds=30.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="portrait.mp4", file_hash="hash2",
            file_path="/clips/portrait.mp4", duration_seconds=30.0,
            width=1080, height=1920, aspect_ratio="9:16",
            orientation="portrait", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selector = OrientationSelector(preferred_orientation="portrait")
    selected = selector.select(clips, count=2)

    assert len(selected) == 2
    assert selected[0].orientation == "portrait"
    assert selected[1].orientation == "landscape"


@pytest.mark.parametrize("orientation", ["portrait", "landscape", "square"])
def test_orientation_selector_accepts_each_video_orientation(orientation):
    width, height = {
        "portrait": (1080, 1920),
        "landscape": (1920, 1080),
        "square": (1080, 1080),
    }[orientation]
    clip = SourceClip(
        id=1, filename=f"{orientation}.mp4", file_hash="hash1",
        file_path=f"/clips/{orientation}.mp4", duration_seconds=30.0,
        width=width, height=height, aspect_ratio="1:1",
        orientation=orientation, file_size_bytes=1000000,
        fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
        times_used=0, created_at=datetime.now(),
        last_used_at=None, metadata_json=None
    )

    selected = OrientationSelector(preferred_orientation=orientation).select(
        [clip], count=1
    )

    assert selected == [clip]
