"""Tests for YouTube upload."""

import pytest
from pathlib import Path
from datetime import datetime
from app.youtube.uploader import MockYouTubeUploader
from app.database.db import Database
from app.metadata.models import YouTubeMetadata


@pytest.fixture
def test_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    with db:
        db.initialize()
        yield db


@pytest.fixture
def mock_uploader(test_db):
    """Create mock YouTube uploader."""
    return MockYouTubeUploader(test_db)


@pytest.fixture
def sample_metadata():
    """Create sample metadata."""
    return YouTubeMetadata(
        title="Test Video",
        description="Test description",
        hashtags=["test", "shorts"],
        visibility="private"
    )


@pytest.fixture
def sample_video(tmp_path):
    """Create sample video file."""
    video_path = tmp_path / "test_video.mp4"
    video_path.write_bytes(b"fake video content")
    return video_path


def test_uploader_configuration(mock_uploader):
    """Test uploader configuration check."""
    assert mock_uploader.is_configured()


def test_uploader_authentication(mock_uploader):
    """Test uploader authentication."""
    mock_uploader.authenticate()
    assert mock_uploader._mock_authenticated is True


def test_upload_video_not_authenticated(mock_uploader, sample_video, sample_metadata):
    """Test upload fails when not authenticated."""
    with pytest.raises(RuntimeError, match="Not authenticated"):
        mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            "JOB-20261002-TEST01"
        )


def test_upload_video_missing_file(mock_uploader, sample_metadata, tmp_path):
    """Test upload fails for missing video."""
    mock_uploader.authenticate()

    with pytest.raises(FileNotFoundError):
        mock_uploader.upload_video(
            tmp_path / "nonexistent.mp4",
            sample_metadata,
            "JOB-20261002-TEST02"
        )


def test_upload_video_success(mock_uploader, sample_video, sample_metadata, test_db):
    """Test successful video upload."""
    mock_uploader.authenticate()

    job_id = "JOB-20261002-TEST03"

    with test_db:
        video_id = mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            job_id
        )

    assert video_id is not None
    assert video_id.startswith("MOCK_")


def test_upload_stores_database_record(mock_uploader, sample_video, sample_metadata, test_db):
    """Test upload stores record in database."""
    mock_uploader.authenticate()

    job_id = "JOB-20261002-TEST04"

    with test_db:
        video_id = mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            job_id
        )

        # Check database
        cursor = test_db.conn.cursor()
        cursor.execute(
            "SELECT * FROM youtube_videos WHERE job_id = ?",
            (job_id,)
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[2] == video_id  # video_id column
    assert row[4] == sample_metadata.title  # title column


def test_duplicate_upload_prevention(mock_uploader, sample_video, sample_metadata, test_db):
    """Test duplicate upload prevention."""
    mock_uploader.authenticate()

    job_id = "JOB-20261002-TEST05"

    with test_db:
        # First upload
        video_id_1 = mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            job_id
        )

        # Second upload (should return same ID)
        video_id_2 = mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            job_id
        )

    assert video_id_1 == video_id_2

    # Check only one record exists
    with test_db:
        cursor = test_db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM youtube_videos WHERE job_id = ?",
            (job_id,)
        )
        count = cursor.fetchone()[0]

    assert count == 1


def test_get_video_url(mock_uploader):
    """Test video URL generation."""
    url = mock_uploader.get_video_url("dQw4w9WgXcQ")
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_upload_with_thumbnail(mock_uploader, sample_video, sample_metadata, test_db, tmp_path):
    """Test upload with thumbnail."""
    mock_uploader.authenticate()

    thumbnail_path = tmp_path / "thumbnail.jpg"
    thumbnail_path.write_bytes(b"fake thumbnail")

    job_id = "JOB-20261002-TEST06"

    with test_db:
        video_id = mock_uploader.upload_video(
            sample_video,
            sample_metadata,
            job_id,
            thumbnail_path=thumbnail_path
        )

    assert video_id is not None


def test_metadata_visibility_respected(mock_uploader, sample_video, test_db):
    """Test visibility setting is stored."""
    mock_uploader.authenticate()

    metadata = YouTubeMetadata(
        title="Test",
        description="Test",
        hashtags=[],
        visibility="unlisted"
    )

    job_id = "JOB-20261002-TEST07"

    with test_db:
        mock_uploader.upload_video(sample_video, metadata, job_id)

        cursor = test_db.conn.cursor()
        cursor.execute(
            "SELECT visibility FROM youtube_videos WHERE job_id = ?",
            (job_id,)
        )
        visibility = cursor.fetchone()[0]

    assert visibility == "unlisted"
