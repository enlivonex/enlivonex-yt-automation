"""YouTube API client for video upload."""

import os
import random
import time
from pathlib import Path
from typing import Optional, Dict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from app.utils.logger import get_logger
from .accounts import read_protected_token, write_protected_token

logger = get_logger()

# Scopes for YouTube upload
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

# Retriable status codes
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# Maximum number of retries
MAX_RETRIES = 3


class YouTubeClient:
    """Client for uploading videos to YouTube."""

    def __init__(
        self,
        client_secret_file: Path,
        token_file: Path
    ):
        """Initialize YouTube client.

        Args:
            client_secret_file: Path to YouTube OAuth client secret JSON
            token_file: Path to store OAuth token
        """
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.service = None
        self._creds = None

    def authenticate(self) -> bool:
        """Authenticate with YouTube API.

        Returns:
            True if authentication successful
        """
        try:
            creds = None

            # Load existing token
            if self.token_file.exists():
                creds = Credentials.from_authorized_user_info(
                    read_protected_token(self.token_file), SCOPES
                )

            # If no valid credentials, let user log in
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("Refreshing expired YouTube credentials")
                    creds.refresh(Request())
                else:
                    if not self.client_secret_file.exists():
                        logger.error(f"YouTube client secret file not found: {self.client_secret_file}")
                        return False

                    logger.info("Starting YouTube OAuth flow")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.client_secret_file),
                        SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # Save credentials
                self.token_file.parent.mkdir(parents=True, exist_ok=True)
                write_protected_token(self.token_file, creds.to_json())
                logger.info(f"Saved YouTube credentials to {self.token_file}")

            self._creds = creds
            self.service = build('youtube', 'v3', credentials=creds)
            logger.info("Successfully authenticated with YouTube")
            return True

        except Exception as e:
            logger.error(f"Failed to authenticate with YouTube: {e}")
            return False

    def upload_video(
        self,
        video_path: Path,
        title: str,
        description: str = "",
        tags: list[str] = None,
        category_id: str = "22",  # People & Blogs
        privacy_status: str = "private"
    ) -> Optional[str]:
        """Upload a video to YouTube.

        Args:
            video_path: Path to video file
            title: Video title
            description: Video description
            tags: List of tags
            category_id: YouTube category ID
            privacy_status: Privacy status (private, unlisted, public)

        Returns:
            YouTube video ID if successful, None otherwise
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        if not video_path.exists():
            logger.error(f"Video file not found: {video_path}")
            return None

        try:
            # Prepare request body
            body = {
                'snippet': {
                    'title': title[:100],  # Max 100 characters
                    'description': description,
                    'tags': tags or [],
                    'categoryId': category_id
                },
                'status': {
                    'privacyStatus': privacy_status,
                    'selfDeclaredMadeForKids': False
                }
            }

            # Create media upload
            media = MediaFileUpload(
                str(video_path),
                chunksize=-1,  # Upload in a single request
                resumable=True
            )

            # Execute upload with retries
            logger.info(f"Uploading video: {video_path.name}")
            request = self.service.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            error = None
            retry = 0

            while response is None:
                try:
                    status, response = request.next_chunk()
                    if response is not None:
                        if 'id' in response:
                            video_id = response['id']
                            logger.info(
                                f"Successfully uploaded video: {video_id} "
                                f"(privacy: {privacy_status})"
                            )
                            return video_id
                        else:
                            logger.error(f"Upload failed: {response}")
                            return None
                    elif status:
                        logger.debug(f"Upload progress: {int(status.progress() * 100)}%")

                except HttpError as e:
                    if e.resp.status in RETRIABLE_STATUS_CODES:
                        error = f"Retriable HTTP error {e.resp.status}: {e.content}"
                    else:
                        logger.error(f"Non-retriable HTTP error: {e}")
                        return None

                except Exception as e:
                    error = f"Unexpected error: {e}"

                if error:
                    logger.warning(error)
                    retry += 1
                    if retry > MAX_RETRIES:
                        logger.error(f"Upload failed after {MAX_RETRIES} retries")
                        return None

                    # Exponential backoff
                    sleep_seconds = random.random() * (2 ** retry)
                    logger.info(f"Retrying in {sleep_seconds:.1f} seconds...")
                    time.sleep(sleep_seconds)

            return None

        except Exception as e:
            logger.error(f"Failed to upload video {video_path.name}: {e}")
            return None

    def get_video_info(self, video_id: str) -> Optional[Dict]:
        """Get information about an uploaded video.

        Args:
            video_id: YouTube video ID

        Returns:
            Video metadata dictionary or None
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            request = self.service.videos().list(
                part='snippet,status',
                id=video_id
            )
            response = request.execute()

            if 'items' in response and len(response['items']) > 0:
                return response['items'][0]
            return None

        except Exception as e:
            logger.error(f"Failed to get video info for {video_id}: {e}")
            return None
