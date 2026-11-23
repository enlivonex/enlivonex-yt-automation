"""YouTube analytics integration."""

from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from ..database.db import Database
from ..database.models import VideoAnalytics
from .accounts import read_protected_token, write_protected_token


class YouTubeAnalytics:
    """Fetch YouTube video analytics."""

    def __init__(
        self,
        db: Database,
        credentials_path: Optional[Path] = None,
        token_path: Optional[Path] = None
    ):
        """Initialize analytics client.

        Args:
            db: Database instance
            credentials_path: Path to OAuth credentials
            token_path: Path to OAuth token
        """
        self.db = db
        self.credentials_path = credentials_path or Path("credentials.json")
        self.token_path = token_path or Path("token.json")
        self._youtube = None

    def authenticate(self):
        """Authenticate with YouTube Data API.

        Raises:
            RuntimeError: If authentication fails
        """
        if not self.credentials_path.exists():
            raise RuntimeError(f"Credentials not found: {self.credentials_path}")

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "Google API packages not installed. Run: "
                "pip install google-auth google-auth-oauthlib google-api-python-client"
            )

        SCOPES = [
            'https://www.googleapis.com/auth/youtube.readonly',
            'https://www.googleapis.com/auth/yt-analytics.readonly'
        ]

        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_info(
                read_protected_token(self.token_path), SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            write_protected_token(self.token_path, creds.to_json())

        self._youtube = build('youtube', 'v3', credentials=creds)

    def fetch_video_analytics(self, video_id: str) -> Dict[str, Any]:
        """Fetch analytics for a video.

        Args:
            video_id: YouTube video ID

        Returns:
            Dictionary with analytics data

        Raises:
            RuntimeError: If not authenticated or fetch fails
        """
        if not self._youtube:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            # Fetch video statistics
            request = self._youtube.videos().list(
                part='statistics,contentDetails',
                id=video_id
            )
            response = request.execute()

            if not response.get('items'):
                raise RuntimeError(f"Video not found: {video_id}")

            item = response['items'][0]
            stats = item.get('statistics', {})

            analytics = {
                'views': int(stats.get('viewCount', 0)),
                'likes': int(stats.get('likeCount', 0)),
                'comments': int(stats.get('commentCount', 0)),
                'watch_time_minutes': None,  # Not available in basic API
                'avg_view_duration_seconds': None  # Not available in basic API
            }

            return analytics

        except Exception as e:
            raise RuntimeError(f"Failed to fetch analytics: {e}")

    def store_analytics_snapshot(
        self,
        video_id: str,
        analytics: Dict[str, Any],
        data_source: str = "youtube_api"
    ):
        """Store analytics snapshot in database.

        Args:
            video_id: YouTube video ID
            analytics: Analytics data dictionary
            data_source: Source of data (youtube_api, manual, etc.)
        """
        import json

        snapshot = VideoAnalytics(
            id=None,
            youtube_video_id=video_id,
            fetched_at=datetime.now(),
            views=analytics.get('views', 0),
            likes=analytics.get('likes', 0),
            comments=analytics.get('comments', 0),
            watch_time_minutes=analytics.get('watch_time_minutes'),
            avg_view_duration_seconds=analytics.get('avg_view_duration_seconds'),
            data_source=data_source,
            raw_data_json=json.dumps(analytics)
        )

        cursor = self.db.conn.cursor()
        cursor.execute("""
            INSERT INTO video_analytics (
                youtube_video_id, fetched_at, views, likes, comments,
                watch_time_minutes, avg_view_duration_seconds,
                data_source, raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.youtube_video_id,
            snapshot.fetched_at.isoformat(),
            snapshot.views,
            snapshot.likes,
            snapshot.comments,
            snapshot.watch_time_minutes,
            snapshot.avg_view_duration_seconds,
            snapshot.data_source,
            snapshot.raw_data_json
        ))
        self.db.conn.commit()

    def get_latest_analytics(self, video_id: str) -> Optional[VideoAnalytics]:
        """Get latest analytics snapshot for video.

        Args:
            video_id: YouTube video ID

        Returns:
            VideoAnalytics or None
        """
        if not self.db.conn:
            return None

        cursor = self.db.conn.cursor()
        cursor.execute("""
            SELECT * FROM video_analytics
            WHERE youtube_video_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
        """, (video_id,))

        row = cursor.fetchone()
        if not row:
            return None

        return VideoAnalytics(
            id=row[0],
            youtube_video_id=row[1],
            fetched_at=datetime.fromisoformat(row[2]),
            views=row[3],
            likes=row[4],
            comments=row[5],
            watch_time_minutes=row[6],
            avg_view_duration_seconds=row[7],
            data_source=row[8],
            raw_data_json=row[9]
        )


class MockYouTubeAnalytics(YouTubeAnalytics):
    """Mock analytics for testing."""

    def __init__(self, db: Database):
        """Initialize mock analytics."""
        super().__init__(db)
        self._mock_authenticated = False

    def authenticate(self):
        """Mock authentication."""
        self._mock_authenticated = True
        self._youtube = object()

    def fetch_video_analytics(self, video_id: str) -> Dict[str, Any]:
        """Mock analytics fetch."""
        if not self._mock_authenticated:
            raise RuntimeError("Not authenticated")

        # Generate mock analytics
        import random
        return {
            'views': random.randint(100, 10000),
            'likes': random.randint(10, 500),
            'comments': random.randint(0, 100),
            'watch_time_minutes': None,
            'avg_view_duration_seconds': None
        }
