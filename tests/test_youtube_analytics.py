"""Tests for YouTube analytics."""

import pytest
from app.youtube.analytics import MockYouTubeAnalytics
from app.database.db import Database


@pytest.fixture
def test_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    with db:
        db.initialize()
        yield db


@pytest.fixture
def mock_analytics(test_db):
    """Create mock analytics client."""
    return MockYouTubeAnalytics(test_db)


def test_analytics_authentication(mock_analytics):
    """Test analytics authentication."""
    mock_analytics.authenticate()
    assert mock_analytics._mock_authenticated is True


def test_fetch_analytics_not_authenticated(mock_analytics):
    """Test fetch fails when not authenticated."""
    with pytest.raises(RuntimeError, match="Not authenticated"):
        mock_analytics.fetch_video_analytics("dQw4w9WgXcQ")


def test_fetch_analytics_success(mock_analytics):
    """Test successful analytics fetch."""
    mock_analytics.authenticate()

    analytics = mock_analytics.fetch_video_analytics("dQw4w9WgXcQ")

    assert 'views' in analytics
    assert 'likes' in analytics
    assert 'comments' in analytics
    assert analytics['views'] > 0


def test_store_analytics_snapshot(mock_analytics, test_db):
    """Test storing analytics snapshot."""
    mock_analytics.authenticate()

    video_id = "dQw4w9WgXcQ"
    analytics = mock_analytics.fetch_video_analytics(video_id)

    with test_db:
        mock_analytics.store_analytics_snapshot(video_id, analytics)

        # Verify stored
        cursor = test_db.conn.cursor()
        cursor.execute(
            "SELECT * FROM video_analytics WHERE youtube_video_id = ?",
            (video_id,)
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[3] == analytics['views']  # views column


def test_get_latest_analytics(mock_analytics, test_db):
    """Test retrieving latest analytics."""
    mock_analytics.authenticate()

    video_id = "test_video_123"

    with test_db:
        # Store first snapshot
        analytics1 = {'views': 100, 'likes': 10, 'comments': 5}
        mock_analytics.store_analytics_snapshot(video_id, analytics1)

        # Store second snapshot
        analytics2 = {'views': 200, 'likes': 20, 'comments': 10}
        mock_analytics.store_analytics_snapshot(video_id, analytics2)

        # Get latest
        latest = mock_analytics.get_latest_analytics(video_id)

    assert latest is not None
    assert latest.views == 200  # Should be second snapshot


def test_get_analytics_no_data(mock_analytics, test_db):
    """Test get analytics with no data."""
    with test_db:
        result = mock_analytics.get_latest_analytics("nonexistent_video")

    assert result is None


def test_analytics_data_source(mock_analytics, test_db):
    """Test data source tracking."""
    mock_analytics.authenticate()

    video_id = "test_video_456"
    analytics = {'views': 1000, 'likes': 50, 'comments': 25}

    with test_db:
        mock_analytics.store_analytics_snapshot(
            video_id,
            analytics,
            data_source="manual"
        )

        latest = mock_analytics.get_latest_analytics(video_id)

    assert latest.data_source == "manual"


def test_analytics_historical_tracking(mock_analytics, test_db):
    """Test historical analytics tracking."""
    mock_analytics.authenticate()

    video_id = "test_video_789"

    with test_db:
        # Store multiple snapshots
        for i in range(5):
            analytics = {'views': (i + 1) * 100, 'likes': (i + 1) * 10, 'comments': i}
            mock_analytics.store_analytics_snapshot(video_id, analytics)

        # Count snapshots
        cursor = test_db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM video_analytics WHERE youtube_video_id = ?",
            (video_id,)
        )
        count = cursor.fetchone()[0]

    assert count == 5


def test_analytics_raw_data_json(mock_analytics, test_db):
    """Test raw data JSON storage."""
    mock_analytics.authenticate()

    video_id = "test_video_json"
    analytics = {
        'views': 5000,
        'likes': 250,
        'comments': 75,
        'custom_metric': 'test_value'
    }

    with test_db:
        mock_analytics.store_analytics_snapshot(video_id, analytics)

        latest = mock_analytics.get_latest_analytics(video_id)

    assert latest.raw_data_json is not None
    assert 'custom_metric' in latest.raw_data_json
