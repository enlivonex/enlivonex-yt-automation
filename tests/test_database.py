"""Tests for database module."""

import tempfile
from pathlib import Path
from datetime import datetime
import pytest
from app.database import Database, MediaItem, MediaStatus
from app.database.models import ClipStatus, SourceClip


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)

    yield db_path

    # Cleanup
    if db_path.exists():
        db_path.unlink()


def test_database_initialization(temp_db):
    """Test database initialization."""
    with Database(temp_db) as db:
        db.initialize()

        # Check tables exist
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_items'")
        result = cursor.fetchone()
        assert result is not None


def test_get_available_clips_includes_previously_used_clips(temp_db):
    with Database(temp_db) as db:
        db.initialize()
        for clip_id, times_used in [(1, 3), (2, 0)]:
            db.insert_clip(SourceClip(
                id=None,
                filename=f"clip_{clip_id}.mp4",
                file_hash=f"hash-{clip_id}",
                file_path=f"clip_{clip_id}.mp4",
                duration_seconds=12.0,
                width=1080,
                height=1920,
                aspect_ratio="9:16",
                orientation="portrait",
                file_size_bytes=1000,
                fps=30.0,
                codec="h264",
                status=ClipStatus.AVAILABLE,
                times_used=times_used,
                created_at=datetime(2026, 1, clip_id),
                last_used_at=None,
                metadata_json=None,
            ))

        clips = db.get_available_clips()

    assert [clip.times_used for clip in clips] == [0, 3]


def test_insert_and_retrieve(temp_db):
    """Test inserting and retrieving media items."""
    with Database(temp_db) as db:
        db.initialize()

        item = MediaItem(
            id=None,
            drive_file_id="test123",
            original_filename="test.jpg",
            source_hash=None,
            status=MediaStatus.DOWNLOADED,
            caption="Test caption",
            input_path="/input/test.jpg",
            output_path=None,
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=None,
            error_message=None
        )

        item_id = db.insert(item)
        assert item_id > 0

        # Retrieve by ID
        retrieved = db.get_by_id(item_id)
        assert retrieved is not None
        assert retrieved.drive_file_id == "test123"
        assert retrieved.original_filename == "test.jpg"
        assert retrieved.status == MediaStatus.DOWNLOADED


def test_nested_database_context_keeps_connection_open_until_outer_exit(temp_db):
    db = Database(temp_db)
    with db:
        outer_connection = db.conn
        with db:
            assert db.conn is outer_connection
        assert db.conn is outer_connection
        assert db.conn.execute("SELECT 1").fetchone()[0] == 1

    assert db.conn is None


def test_get_by_drive_file_id(temp_db):
    """Test retrieving by Drive file ID."""
    with Database(temp_db) as db:
        db.initialize()

        item = MediaItem(
            id=None,
            drive_file_id="drive_abc123",
            original_filename="test.jpg",
            source_hash=None,
            status=MediaStatus.DISCOVERED,
            caption=None,
            input_path=None,
            output_path=None,
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=None,
            error_message=None
        )

        db.insert(item)

        # Retrieve
        retrieved = db.get_by_drive_file_id("drive_abc123")
        assert retrieved is not None
        assert retrieved.original_filename == "test.jpg"

        # Non-existent
        not_found = db.get_by_drive_file_id("nonexistent")
        assert not_found is None


def test_update_status(temp_db):
    """Test updating item status."""
    with Database(temp_db) as db:
        db.initialize()

        item = MediaItem(
            id=None,
            drive_file_id="test_update",
            original_filename="test.jpg",
            source_hash=None,
            status=MediaStatus.DOWNLOADED,
            caption=None,
            input_path="/input/test.jpg",
            output_path=None,
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=None,
            error_message=None
        )

        item_id = db.insert(item)

        # Update status
        db.update_status(item_id, MediaStatus.PROCESSED)

        # Retrieve and verify
        updated = db.get_by_id(item_id)
        assert updated.status == MediaStatus.PROCESSED
        assert updated.processed_at is not None


def test_is_processed(temp_db):
    """Test checking if file is already processed."""
    with Database(temp_db) as db:
        db.initialize()

        # Not processed
        assert not db.is_processed("new_file")

        # Insert item
        item = MediaItem(
            id=None,
            drive_file_id="processed_file",
            original_filename="test.jpg",
            source_hash=None,
            status=MediaStatus.PROCESSED,
            caption="Test",
            input_path="/input/test.jpg",
            output_path="/output/test.jpg",
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=datetime.now(),
            error_message=None
        )

        db.insert(item)

        # Now processed
        assert db.is_processed("processed_file")


def test_get_by_status(temp_db):
    """Test retrieving items by status."""
    with Database(temp_db) as db:
        db.initialize()

        # Insert multiple items with different statuses
        for i in range(3):
            item = MediaItem(
                id=None,
                drive_file_id=f"file_{i}",
                original_filename=f"test_{i}.jpg",
                source_hash=None,
                status=MediaStatus.PROCESSED if i < 2 else MediaStatus.FAILED,
                caption=None,
                input_path=None,
                output_path=None,
                youtube_video_id=None,
                created_at=datetime.now(),
                processed_at=None,
                error_message=None
            )
            db.insert(item)

        # Get processed items
        processed = db.get_by_status(MediaStatus.PROCESSED)
        assert len(processed) == 2

        # Get failed items
        failed = db.get_by_status(MediaStatus.FAILED)
        assert len(failed) == 1


def test_count_by_status(temp_db):
    """Test counting items by status."""
    with Database(temp_db) as db:
        db.initialize()

        # Initially zero
        assert db.count_by_status(MediaStatus.PROCESSED) == 0

        # Insert items
        for i in range(5):
            item = MediaItem(
                id=None,
                drive_file_id=f"file_{i}",
                original_filename=f"test_{i}.jpg",
                source_hash=None,
                status=MediaStatus.PROCESSED,
                caption=None,
                input_path=None,
                output_path=None,
                youtube_video_id=None,
                created_at=datetime.now(),
                processed_at=None,
                error_message=None
            )
            db.insert(item)

        assert db.count_by_status(MediaStatus.PROCESSED) == 5


def test_duplicate_drive_file_id(temp_db):
    """Test that duplicate Drive file IDs are prevented."""
    with Database(temp_db) as db:
        db.initialize()

        item1 = MediaItem(
            id=None,
            drive_file_id="duplicate_test",
            original_filename="test1.jpg",
            source_hash=None,
            status=MediaStatus.DOWNLOADED,
            caption=None,
            input_path=None,
            output_path=None,
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=None,
            error_message=None
        )

        db.insert(item1)

        # Try to insert duplicate
        item2 = MediaItem(
            id=None,
            drive_file_id="duplicate_test",
            original_filename="test2.jpg",
            source_hash=None,
            status=MediaStatus.DOWNLOADED,
            caption=None,
            input_path=None,
            output_path=None,
            youtube_video_id=None,
            created_at=datetime.now(),
            processed_at=None,
            error_message=None
        )

        with pytest.raises(Exception):
            db.insert(item2)
