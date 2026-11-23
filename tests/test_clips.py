"""Tests for clip library management."""

import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch
from app.clips.manager import ClipManager
from app.clips.selector import (
    RandomSelector, LeastUsedSelector, OrientationSelector,
    DurationSelector, CompositeSelector
)
from app.database.db import Database
from app.database.models import SourceClip, ClipStatus


@pytest.fixture
def temp_clips_dir(tmp_path):
    """Create temporary clips directory."""
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    return clips_dir


@pytest.fixture
def test_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    with db:
        db.initialize()
        yield db


def test_clip_manager_initialization(test_db, temp_clips_dir):
    """Test ClipManager initialization."""
    manager = ClipManager(test_db, temp_clips_dir)

    assert manager.db == test_db
    assert manager.clips_dir == temp_clips_dir
    assert temp_clips_dir.exists()


def test_ingest_clip_no_ffmpeg(test_db, temp_clips_dir):
    """Test clip ingestion fails without FFmpeg."""
    test_file = temp_clips_dir / "test.mp4"
    test_file.write_text("fake video")

    # Mock FFmpeg as unavailable
    mock_ffmpeg = Mock()
    mock_ffmpeg.is_available.return_value = False

    manager = ClipManager(test_db, temp_clips_dir, ffmpeg=mock_ffmpeg)

    with pytest.raises(RuntimeError, match="FFmpeg not available"):
        manager.ingest_clip(test_file)


def test_get_clip_stats_empty(test_db, temp_clips_dir):
    """Test stats on empty library."""
    manager = ClipManager(test_db, temp_clips_dir)

    with test_db:
        stats = manager.get_clip_stats()

    assert stats["total"] == 0
    assert stats["unused"] == 0
    assert stats["total_duration_seconds"] == 0.0


def test_random_selector():
    """Test random clip selection."""
    clips = [
        SourceClip(
            id=i, filename=f"clip{i}.mp4", file_hash=f"hash{i}",
            file_path=f"/clips/clip{i}.mp4", duration_seconds=10.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
        for i in range(5)
    ]

    selector = RandomSelector()
    selected = selector.select(clips, count=2)

    assert len(selected) == 2
    assert all(c in clips for c in selected)


def test_least_used_selector():
    """Test least-used selection strategy."""
    clips = [
        SourceClip(
            id=1, filename="used.mp4", file_hash="hash1",
            file_path="/clips/used.mp4", duration_seconds=10.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=5, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="unused.mp4", file_hash="hash2",
            file_path="/clips/unused.mp4", duration_seconds=10.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selector = LeastUsedSelector()
    selected = selector.select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].filename == "unused.mp4"


def test_orientation_selector():
    """Test orientation-based selection."""
    clips = [
        SourceClip(
            id=1, filename="portrait.mp4", file_hash="hash1",
            file_path="/clips/portrait.mp4", duration_seconds=10.0,
            width=1080, height=1920, aspect_ratio="9:16",
            orientation="portrait", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="landscape.mp4", file_hash="hash2",
            file_path="/clips/landscape.mp4", duration_seconds=10.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selector = OrientationSelector(preferred_orientation="portrait")
    selected = selector.select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].orientation == "portrait"


def test_duration_selector():
    """Test duration-based selection."""
    clips = [
        SourceClip(
            id=1, filename="short.mp4", file_hash="hash1",
            file_path="/clips/short.mp4", duration_seconds=5.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="medium.mp4", file_hash="hash2",
            file_path="/clips/medium.mp4", duration_seconds=30.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=3, filename="long.mp4", file_hash="hash3",
            file_path="/clips/long.mp4", duration_seconds=120.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    selector = DurationSelector(min_duration=10.0, max_duration=60.0)
    selected = selector.select(clips, count=2)

    assert len(selected) == 2
    assert selected[0].filename == "medium.mp4"


def test_composite_selector():
    """Test composite selector combining strategies."""
    clips = [
        SourceClip(
            id=1, filename="portrait_short.mp4", file_hash="hash1",
            file_path="/clips/portrait_short.mp4", duration_seconds=5.0,
            width=1080, height=1920, aspect_ratio="9:16",
            orientation="portrait", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=2, filename="portrait_long.mp4", file_hash="hash2",
            file_path="/clips/portrait_long.mp4", duration_seconds=30.0,
            width=1080, height=1920, aspect_ratio="9:16",
            orientation="portrait", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        ),
        SourceClip(
            id=3, filename="landscape.mp4", file_hash="hash3",
            file_path="/clips/landscape.mp4", duration_seconds=30.0,
            width=1920, height=1080, aspect_ratio="16:9",
            orientation="landscape", file_size_bytes=1000000,
            fps=30.0, codec="h264", status=ClipStatus.AVAILABLE,
            times_used=0, created_at=datetime.now(),
            last_used_at=None, metadata_json=None
        )
    ]

    # Select portrait clips 10-60 seconds long
    selector = CompositeSelector([
        OrientationSelector(preferred_orientation="portrait"),
        DurationSelector(min_duration=10.0, max_duration=60.0),
        LeastUsedSelector()
    ])

    selected = selector.select(clips, count=1)

    assert len(selected) == 1
    assert selected[0].filename == "portrait_long.mp4"
