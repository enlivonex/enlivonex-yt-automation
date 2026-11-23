"""Google Drive client for file discovery and download."""

import os
from pathlib import Path
import re
import tempfile
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

from app.utils.logger import get_logger
from app.youtube.accounts import read_protected_token, write_protected_token

logger = get_logger()

# If modifying these scopes, delete the token file
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


class DriveClient:
    """Client for interacting with Google Drive API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_file: Path,
        folder_id: str
    ):
        """Initialize Drive client.

        Args:
            client_id: Google OAuth client ID
            client_secret: Google OAuth client secret
            token_file: Path to store OAuth token
            folder_id: Google Drive folder ID to monitor
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.folder_id = folder_id
        self.service = None
        self._creds = None

    def authenticate(self, raise_errors: bool = False) -> bool:
        """Authenticate with Google Drive API.

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
                    logger.info("Refreshing expired Google Drive credentials")
                    creds.refresh(Request())
                else:
                    logger.info("Starting Google Drive OAuth flow")

                    # Create OAuth config
                    client_config = {
                        "installed": {
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "redirect_uris": ["http://localhost"],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                        }
                    }

                    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                    creds = flow.run_local_server(port=0)

                # Save credentials
                self.token_file.parent.mkdir(parents=True, exist_ok=True)

            write_protected_token(self.token_file, creds.to_json())
            self._creds = creds
            self.service = build('drive', 'v3', credentials=creds)
            logger.info("Successfully authenticated with Google Drive")
            return True

        except Exception as e:
            logger.error(f"Failed to authenticate with Google Drive: {e}")
            if raise_errors:
                raise RuntimeError(
                    f"Failed to authenticate with Google Drive: {e}"
                ) from e
            return False

    def list_files(
        self,
        supported_formats: List[str],
        page_size: int = 100,
        raise_errors: bool = False,
    ) -> List[Dict[str, str]]:
        """List files in the configured Drive folder.

        Args:
            supported_formats: List of supported file extensions
            page_size: Number of files per page

        Returns:
            List of file metadata dictionaries
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            mime_types = {
                "avi": "video/x-msvideo",
                "flv": "video/x-flv",
                "mkv": "video/x-matroska",
                "mov": "video/quicktime",
                "mp4": "video/mp4",
                "webm": "video/webm",
            }
            extensions = {item.lower().lstrip(".") for item in supported_formats}
            mime_query_parts = sorted(
                {
                    f"mimeType='{mime_types[extension]}'"
                    for extension in extensions
                    if extension in mime_types
                }
            )
            query = f"'{self.folder_id}' in parents and trashed=false"
            if mime_query_parts:
                query += f" and ({' or '.join(mime_query_parts)})"

            files = []
            page_token = None
            while True:
                results = self.service.files().list(
                    q=query,
                    pageSize=max(1, min(page_size, 1000)),
                    pageToken=page_token,
                    fields="nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                ).execute()
                files.extend(
                    item
                    for item in results.get("files", [])
                    if Path(str(item.get("name", ""))).suffix.lower().lstrip(".")
                    in extensions
                )
                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            logger.info(f"Found {len(files)} files in Drive folder")

            return files

        except HttpError as e:
            if e.resp.status == 404:
                logger.error(f"Drive folder not found: {self.folder_id}")
            elif e.resp.status == 403:
                logger.error("Access denied to Drive folder")
            else:
                logger.error(f"Drive API error: {e}")
            if raise_errors:
                raise RuntimeError(f"Could not list Google Drive files: {e}") from e
            return []
        except Exception as e:
            logger.error(f"Failed to list Drive files: {e}")
            if raise_errors:
                raise RuntimeError(f"Could not list Google Drive files: {e}") from e
            return []

    def download_file(
        self,
        file_id: str,
        filename: str,
        output_dir: Path,
        raise_errors: bool = False,
    ) -> Optional[Path]:
        """Download a file from Google Drive.

        Args:
            file_id: Google Drive file ID
            filename: Original filename
            output_dir: Directory to save file

        Returns:
            Path to downloaded file, or None if failed
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
            safe_filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_filename)
            if safe_filename in {"", ".", ".."}:
                raise ValueError("Google Drive filename is empty or invalid.")
            output_path = output_dir / safe_filename
            if output_path.exists():
                raise FileExistsError(
                    f"Refusing to overwrite an existing file: {output_path.name}"
                )

            # Get file
            request = self.service.files().get_media(fileId=file_id)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".drive-download-", suffix=".part", dir=output_dir
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as downloaded_file:
                    downloader = MediaIoBaseDownload(downloaded_file, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                        if status:
                            logger.debug(
                                "Download progress: %s%%",
                                int(status.progress() * 100),
                            )
                if output_path.exists():
                    raise FileExistsError(
                        f"Refusing to overwrite an existing file: {output_path.name}"
                    )
                os.replace(temporary_path, output_path)
            finally:
                temporary_path.unlink(missing_ok=True)

            logger.info(f"Downloaded file: {filename} ({output_path.stat().st_size} bytes)")
            return output_path

        except HttpError as e:
            if e.resp.status == 404:
                logger.error(f"File not found: {file_id}")
            elif e.resp.status == 403:
                logger.error(f"Access denied for file: {file_id}")
            else:
                logger.error(f"Drive API error downloading {filename}: {e}")
            if raise_errors:
                raise RuntimeError(
                    f"Could not download Google Drive file {filename}: {e}"
                ) from e
            return None
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            if raise_errors:
                raise RuntimeError(
                    f"Could not download Google Drive file {filename}: {e}"
                ) from e
            return None

    def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        """Get metadata for a specific file.

        Args:
            file_id: Google Drive file ID

        Returns:
            File metadata dictionary or None
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            file = self.service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, createdTime, modifiedTime"
            ).execute()
            return file
        except Exception as e:
            logger.error(f"Failed to get file metadata for {file_id}: {e}")
            return None

    def verify_folder_access(self, raise_errors: bool = False) -> bool:
        """Verify access to the configured Drive folder.

        Returns:
            True if folder is accessible
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            folder = self.service.files().get(
                fileId=self.folder_id,
                fields="id, name, mimeType"
            ).execute()

            if folder.get('mimeType') != 'application/vnd.google-apps.folder':
                logger.error(f"ID {self.folder_id} is not a folder")
                return False

            logger.info(f"Verified access to folder: {folder.get('name')}")
            return True

        except HttpError as e:
            if e.resp.status == 404:
                logger.error(f"Folder not found: {self.folder_id}")
            elif e.resp.status == 403:
                logger.error(f"Access denied to folder: {self.folder_id}")
            else:
                logger.error(f"Error verifying folder: {e}")
            if raise_errors:
                raise RuntimeError(
                    f"Could not access Google Drive folder {self.folder_id}: {e}"
                ) from e
            return False
        except Exception as e:
            logger.error(f"Failed to verify folder access: {e}")
            if raise_errors:
                raise RuntimeError(
                    f"Could not access Google Drive folder {self.folder_id}: {e}"
                ) from e
            return False
