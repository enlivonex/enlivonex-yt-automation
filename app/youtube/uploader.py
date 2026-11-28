"""YouTube upload integration."""

import logging
import time
from pathlib import Path
from typing import Callable, Optional, TypeVar
from datetime import datetime

import requests

from ..database.db import Database
from ..database.models import YouTubeVideo
from ..metadata.models import YouTubeMetadata
from .accounts import read_protected_token, write_protected_token

logger = logging.getLogger(__name__)
OAUTH_HTTP_TIMEOUT = (10, 45)
OAUTH_CONNECT_ATTEMPTS = 3
_OAuthResult = TypeVar("_OAuthResult")


def _retry_oauth_connection(operation: Callable[[], _OAuthResult]) -> _OAuthResult:
    """Retry transient connection timeouts during OAuth token exchanges."""
    for attempt in range(OAUTH_CONNECT_ATTEMPTS):
        try:
            return operation()
        except requests.exceptions.ConnectTimeout as error:
            if attempt + 1 == OAUTH_CONNECT_ATTEMPTS:
                raise RuntimeError(
                    "Could not connect to Google's OAuth token service after "
                    f"{OAUTH_CONNECT_ATTEMPTS} attempts. Check this PC's internet "
                    "connection and whether its firewall, VPN, or security "
                    "software allows Enlivonex to reach oauth2.googleapis.com, "
                    "then try connecting again."
                ) from error
            delay = attempt + 1
            logger.warning(
                "Google OAuth token connection timed out; retrying in %s second(s).",
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("Google OAuth connection retry ended unexpectedly.")


class YouTubeReauthorizationRequired(RuntimeError):
    """Raised when an existing YouTube profile needs a fresh OAuth grant."""


class YouTubeUploader:
    """Handle YouTube video uploads with OAuth."""

    def __init__(
        self,
        db: Database,
        credentials_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
        request_timeout_seconds: int = 120,
        no_progress_timeout_seconds: int = 180,
    ):
        """Initialize YouTube uploader.

        Args:
            db: Database instance
            credentials_path: Path to OAuth client credentials JSON
            token_path: Path to store OAuth token
        """
        self.db = db
        self.credentials_path = credentials_path or Path("credentials.json")
        self.token_path = token_path or Path("token.json")
        self.request_timeout_seconds = request_timeout_seconds
        self.no_progress_timeout_seconds = no_progress_timeout_seconds
        self._youtube = None

    def is_configured(self) -> bool:
        """Check if YouTube upload is configured.

        Returns:
            True if credentials file exists
        """
        return self.credentials_path.exists()

    def authenticate(
        self,
        allow_new_authorization: bool = True,
        force_reauthorization: bool = False,
    ):
        """Authenticate with YouTube using OAuth 2.0.

        Raises:
            RuntimeError: If credentials not found or authentication fails
        """
        if not self.credentials_path.exists():
            raise RuntimeError(
                f"YouTube credentials not found at {self.credentials_path}. "
                "Download OAuth client credentials from Google Cloud Console."
            )

        try:
            from google.auth.exceptions import RefreshError
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            from google_auth_httplib2 import AuthorizedHttp
            import httplib2
            from oauthlib.oauth2.rfc6749.errors import InvalidClientError
        except ImportError:
            raise RuntimeError(
                "Google API packages not installed. Run: "
                "pip install google-auth google-auth-oauthlib google-api-python-client"
            )

        class TimeoutRequest(Request):
            def __call__(
                self,
                url,
                method="GET",
                body=None,
                headers=None,
                timeout=OAUTH_HTTP_TIMEOUT,
                **kwargs,
            ):
                return super().__call__(
                    url,
                    method=method,
                    body=body,
                    headers=headers,
                    timeout=timeout,
                    **kwargs,
                )

        SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

        creds = None

        # Load existing token
        if self.token_path.exists() and not force_reauthorization:
            try:
                creds = Credentials.from_authorized_user_info(
                    read_protected_token(self.token_path), SCOPES
                )
            except (OSError, RuntimeError, ValueError) as error:
                if not allow_new_authorization:
                    raise YouTubeReauthorizationRequired(
                        "The saved YouTube token cannot be used. Reconnect this "
                        "account from YouTube Setup."
                    ) from error

        # Refresh or get new token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    _retry_oauth_connection(
                        lambda: creds.refresh(TimeoutRequest())
                    )
                except RefreshError as error:
                    if "invalid_client" in str(error).casefold():
                        raise self._invalid_oauth_client_error() from error
                    if not allow_new_authorization:
                        raise YouTubeReauthorizationRequired(
                            "YouTube authorization has expired or been revoked. "
                            "Reconnect this account from YouTube Setup."
                        ) from error
                    creds = None
            if not creds or not creds.valid:
                if not allow_new_authorization:
                    raise YouTubeReauthorizationRequired(
                        "The selected YouTube account is not authorized. "
                        "Connect it from YouTube Setup again before uploading."
                    )
                class RetryingInstalledAppFlow(InstalledAppFlow):
                    def fetch_token(self, **kwargs):
                        kwargs.setdefault("timeout", OAUTH_HTTP_TIMEOUT)
                        return _retry_oauth_connection(
                            lambda: super(
                                RetryingInstalledAppFlow, self
                            ).fetch_token(**kwargs)
                        )

                flow = RetryingInstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                try:
                    creds = flow.run_local_server(
                        port=0,
                        access_type="offline",
                        prompt="consent select_account",
                        timeout_seconds=300,
                    )
                except InvalidClientError as error:
                    raise self._invalid_oauth_client_error() from error

        media_http = self._create_media_http(httplib2)
        authorized_http = AuthorizedHttp(creds, http=media_http)
        self._youtube = build(
            'youtube', 'v3', http=authorized_http, cache_discovery=False
        )
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        write_protected_token(self.token_path, creds.to_json())

    def _invalid_oauth_client_error(self) -> RuntimeError:
        return RuntimeError(
            "YouTube rejected the OAuth client configured at "
            f"'{self.credentials_path}'. Use a matching OAuth 2.0 Desktop app "
            "JSON downloaded from Google Cloud Console; Google AI Studio/Gemini "
            "API keys cannot be used for YouTube sign-in."
        )

    def _create_media_http(self, httplib2_module=None):
        """Preserve YouTube's HTTP 308 resumable status for the API client."""
        if httplib2_module is None:
            import httplib2 as httplib2_module
        http = httplib2_module.Http(timeout=self.request_timeout_seconds)
        http.redirect_codes = (300, 301, 302, 303, 307)
        return http

    def upload_video(
        self,
        video_path: Path,
        metadata: YouTubeMetadata,
        job_id: str,
        thumbnail_path: Optional[Path] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        account_id: Optional[str] = None,
        admin_key: Optional[str] = None,
        enforce_daily_limit: bool = True,
    ) -> str:
        """Upload video to YouTube.

        Args:
            video_path: Path to video file
            metadata: YouTube metadata
            job_id: Job ID for tracking
            thumbnail_path: Optional thumbnail image
            progress_callback: Optional callback receiving upload percentage
            account_id: Optional YouTube account profile ID
            admin_key: Optional administrator bypass key
            enforce_daily_limit: Whether to enforce the 2 videos/day limit

        Returns:
            YouTube video ID

        Raises:
            RuntimeError: If upload fails or not authenticated
            DailyLimitExceededError: If daily upload limit is reached
        """
        if not self._youtube:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        # Check daily upload limit for this account
        if enforce_daily_limit:
            from ..limits import check_upload_limit, DailyLimitExceededError
            allowed, limit_message, _ = check_upload_limit(
                database_path=self.db.db_path,
                account_id=account_id,
                admin_key=admin_key,
            )
            if not allowed:
                raise DailyLimitExceededError(limit_message)

        # Check for duplicate upload
        if self._is_already_uploaded(job_id):
            existing = self._get_uploaded_video(job_id)
            return existing.video_id

        # Prepare request body
        body = metadata.to_dict()

        # Upload video
        try:
            from googleapiclient.http import MediaFileUpload

            media = MediaFileUpload(
                str(video_path),
                chunksize=4 * 1024 * 1024,
                resumable=True,
                mimetype='video/mp4'
            )

            request = self._youtube.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            last_progress = 0.0
            last_progress_at = time.monotonic()
            transient_failures = 0
            while response is None:
                try:
                    status, response = request.next_chunk(num_retries=2)
                except Exception as error:
                    if self._is_unauthorized_error(error):
                        raise YouTubeReauthorizationRequired(
                            "YouTube rejected this account's authorization. "
                            "Reconnect the selected account and retry the upload."
                        ) from error
                    if not (
                        isinstance(error, OSError)
                        or self._is_httplib2_transport_error(error)
                    ):
                        raise
                    transient_failures += 1
                    elapsed_without_progress = (
                        time.monotonic() - last_progress_at
                    )
                    if elapsed_without_progress >= self.no_progress_timeout_seconds:
                        raise TimeoutError(
                            "YouTube upload timed out without making progress "
                            f"for {self.no_progress_timeout_seconds} seconds. "
                            "The resumable upload was retried; check YouTube "
                            "Studio before starting a new upload."
                        ) from error

                    delay = min(2 ** min(transient_failures - 1, 3), 8)
                    logger.warning(
                        "Transient YouTube upload error for job %s; retrying "
                        "the resumable request in %s seconds (attempt %s): %s",
                        job_id,
                        delay,
                        transient_failures,
                        error,
                    )
                    remaining = (
                        self.no_progress_timeout_seconds
                        - elapsed_without_progress
                    )
                    time.sleep(min(delay, max(0, remaining)))
                    continue
                transient_failures = 0
                if status:
                    progress = max(last_progress, float(status.progress()))
                    if progress > last_progress:
                        last_progress = progress
                        last_progress_at = time.monotonic()
                        if progress_callback:
                            progress_callback(int(progress * 100))
                if (
                    response is None
                    and time.monotonic() - last_progress_at
                    > self.no_progress_timeout_seconds
                ):
                    raise TimeoutError(
                        "YouTube upload made no progress for "
                        f"{self.no_progress_timeout_seconds} seconds. Check your "
                        "network and retry; verify YouTube Studio before retrying "
                        "to avoid creating a duplicate."
                    )

            if not isinstance(response, dict) or not response.get("id"):
                raise RuntimeError("YouTube completed the upload without returning a video ID.")
            video_id = response['id']
            if progress_callback:
                progress_callback(100)

            # Upload thumbnail if provided
            if thumbnail_path and thumbnail_path.exists():
                self._upload_thumbnail(video_id, thumbnail_path)

            # Store in database
            self._store_upload_record(job_id, video_id, metadata)
            if self.db and hasattr(self.db, "db_path"):
                from ..limits import record_video_upload
                record_video_upload(self.db.db_path, video_id, account_id=account_id)

            return video_id

        except YouTubeReauthorizationRequired:
            raise
        except Exception as e:
            logger.exception("YouTube upload failed for job %s", job_id)
            raise RuntimeError(f"YouTube upload failed: {e}") from e

    @staticmethod
    def _is_unauthorized_error(error: Exception) -> bool:
        response = getattr(error, "resp", None)
        return getattr(response, "status", None) == 401

    @staticmethod
    def _is_httplib2_transport_error(error: Exception) -> bool:
        try:
            import httplib2
        except ImportError:
            return False
        return isinstance(error, httplib2.HttpLib2Error)

    def _upload_thumbnail(self, video_id: str, thumbnail_path: Path):
        """Upload thumbnail for video.

        Args:
            video_id: YouTube video ID
            thumbnail_path: Path to thumbnail image
        """
        try:
            from googleapiclient.http import MediaFileUpload

            media = MediaFileUpload(
                str(thumbnail_path),
                mimetype='image/jpeg',
                resumable=False
            )

            self._youtube.thumbnails().set(
                videoId=video_id,
                media_body=media
            ).execute()

        except Exception:
            logger.exception("Thumbnail upload failed for YouTube video %s", video_id)

    def _is_already_uploaded(self, job_id: str) -> bool:
        """Check if job was already uploaded.

        Args:
            job_id: Job ID

        Returns:
            True if already uploaded
        """
        if not self.db.conn:
            return False

        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM youtube_videos WHERE job_id = ?",
            (job_id,)
        )
        return cursor.fetchone()[0] > 0

    def _get_uploaded_video(self, job_id: str) -> Optional[YouTubeVideo]:
        """Get uploaded video record.

        Args:
            job_id: Job ID

        Returns:
            YouTubeVideo or None
        """
        if not self.db.conn:
            return None

        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT * FROM youtube_videos WHERE job_id = ? LIMIT 1",
            (job_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        return YouTubeVideo(
            id=row[0],
            job_id=row[1],
            video_id=row[2],
            url=row[3],
            title=row[4],
            description=row[5],
            hashtags=row[6],
            visibility=row[7],
            upload_timestamp=datetime.fromisoformat(row[8]),
            status=row[9]
        )

    def _store_upload_record(
        self,
        job_id: str,
        video_id: str,
        metadata: YouTubeMetadata
    ):
        """Store upload record in database.

        Args:
            job_id: Job ID
            video_id: YouTube video ID
            metadata: Video metadata
        """
        video = YouTubeVideo(
            id=None,
            job_id=job_id,
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=metadata.title,
            description=metadata.description,
            hashtags=",".join(metadata.hashtags),
            visibility=metadata.visibility,
            upload_timestamp=datetime.now(),
            status="uploaded"
        )

        cursor = self.db.conn.cursor()
        cursor.execute("""
            INSERT INTO youtube_videos (
                job_id, video_id, url, title, description,
                hashtags, visibility, upload_timestamp, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video.job_id, video.video_id, video.url, video.title,
            video.description, video.hashtags, video.visibility,
            video.upload_timestamp.isoformat(), video.status
        ))
        self.db.conn.commit()

    def get_video_url(self, video_id: str) -> str:
        """Get YouTube URL for video.

        Args:
            video_id: YouTube video ID

        Returns:
            Full YouTube URL
        """
        return f"https://www.youtube.com/watch?v={video_id}"


class MockYouTubeUploader(YouTubeUploader):
    """Mock YouTube uploader for testing."""

    def __init__(self, db: Database):
        """Initialize mock uploader."""
        super().__init__(db)
        self._mock_authenticated = False

    def is_configured(self) -> bool:
        """Mock always configured."""
        return True

    def authenticate(self):
        """Mock authentication."""
        self._mock_authenticated = True
        self._youtube = object()  # Mock YouTube client

    def upload_video(
        self,
        video_path: Path,
        metadata: YouTubeMetadata,
        job_id: str,
        thumbnail_path: Optional[Path] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        account_id: Optional[str] = None,
        admin_key: Optional[str] = None,
        enforce_daily_limit: bool = False,
    ) -> str:
        """Mock video upload."""
        if not self._mock_authenticated:
            raise RuntimeError("Not authenticated")

        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        if enforce_daily_limit:
            from ..limits import check_upload_limit, DailyLimitExceededError
            allowed, limit_message, _ = check_upload_limit(
                database_path=self.db.db_path,
                account_id=account_id,
                admin_key=admin_key,
            )
            if not allowed:
                raise DailyLimitExceededError(limit_message)

        # Check for duplicate
        if self._is_already_uploaded(job_id):
            existing = self._get_uploaded_video(job_id)
            return existing.video_id

        # Generate mock video ID
        video_id = f"MOCK_{job_id[-6:]}"

        # Store record
        if self.db.conn:
            self._store_upload_record(job_id, video_id, metadata)
            if hasattr(self.db, "db_path"):
                from ..limits import record_video_upload
                record_video_upload(self.db.db_path, video_id, account_id=account_id)

        if progress_callback:
            progress_callback(100)
        return video_id
