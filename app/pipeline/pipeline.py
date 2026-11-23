"""Main processing pipeline."""

from pathlib import Path
from typing import Optional, List
from datetime import datetime

from app.config import Settings
from app.database import Database, MediaItem, MediaStatus
from app.drive import DriveClient
from app.media import MediaValidator, MediaProcessor
from app.captions import CaptionGenerator
from app.youtube import YouTubeClient
from app.utils.logger import get_logger

logger = get_logger()


class Pipeline:
    """Main automation pipeline orchestrator."""

    def __init__(self, settings: Settings):
        """Initialize pipeline with settings."""
        self.settings = settings

        # Initialize components
        self.validator = MediaValidator(
            supported_image_formats=settings.supported_image_formats,
            supported_video_formats=settings.supported_video_formats,
            max_file_size_mb=settings.max_file_size_mb
        )

        self.processor = MediaProcessor(
            caption_font_size=settings.caption_font_size,
            caption_padding=settings.caption_padding,
            caption_bg_color=settings.caption_bg_color,
            caption_text_color=settings.caption_text_color,
            ffmpeg_path=settings.ffmpeg_path,
            ffprobe_path=settings.ffprobe_path
        )

        # Drive client (initialized on demand)
        self._drive_client: Optional[DriveClient] = None

        # YouTube client (initialized on demand)
        self._youtube_client: Optional[YouTubeClient] = None

    def get_drive_client(self) -> Optional[DriveClient]:
        """Get or create Drive client."""
        if not self.settings.is_google_configured():
            logger.warning("Google Drive not configured")
            return None

        if not self._drive_client:
            self._drive_client = DriveClient(
                client_id=self.settings.google_client_id,
                client_secret=self.settings.google_client_secret,
                token_file=self.settings.google_token_file,
                folder_id=self.settings.google_drive_folder_id
            )

            if not self._drive_client.authenticate():
                logger.error("Failed to authenticate with Google Drive")
                return None

            if not self._drive_client.verify_folder_access():
                logger.error("Cannot access Google Drive folder")
                return None

        return self._drive_client

    def get_youtube_client(self) -> Optional[YouTubeClient]:
        """Get or create YouTube client."""
        if not self.settings.is_youtube_configured():
            logger.warning("YouTube not configured")
            return None

        if not self._youtube_client:
            self._youtube_client = YouTubeClient(
                client_secret_file=self.settings.youtube_client_secret_file,
                token_file=self.settings.youtube_token_file
            )

            if not self._youtube_client.authenticate():
                logger.error("Failed to authenticate with YouTube")
                return None

        return self._youtube_client

    def process_local_files(
        self,
        input_dir: Path,
        caption_generator: CaptionGenerator,
        force: bool = False
    ) -> int:
        """Process local files without Drive integration.

        Args:
            input_dir: Directory containing input files
            caption_generator: Caption generator instance
            force: Force reprocessing of already-processed files

        Returns:
            Number of files successfully processed
        """
        logger.info(f"Processing local files from: {input_dir}")

        if not input_dir.exists():
            logger.error(f"Input directory does not exist: {input_dir}")
            return 0

        processed_count = 0

        with Database(self.settings.database_path) as db:
            db.initialize()

            # Find all supported files
            all_files = []
            for ext in self.settings.supported_image_formats + self.settings.supported_video_formats:
                all_files.extend(input_dir.glob(f"*.{ext}"))
                all_files.extend(input_dir.glob(f"*.{ext.upper()}"))

            logger.info(f"Found {len(all_files)} potential files")

            for file_path in all_files:
                try:
                    # Use filename as pseudo-Drive-ID for local processing
                    pseudo_drive_id = f"local_{file_path.name}"

                    # Check if already processed
                    if not force and db.is_processed(pseudo_drive_id):
                        logger.info(f"Skipping already processed: {file_path.name}")
                        continue

                    # Validate file
                    is_valid, error = self.validator.validate_file(file_path)
                    if not is_valid:
                        logger.warning(f"Skipping invalid file {file_path.name}: {error}")
                        continue

                    # Create database entry
                    item = MediaItem(
                        id=None,
                        drive_file_id=pseudo_drive_id,
                        original_filename=file_path.name,
                        source_hash=None,
                        status=MediaStatus.DOWNLOADED,
                        caption=None,
                        input_path=str(file_path),
                        output_path=None,
                        youtube_video_id=None,
                        created_at=datetime.now(),
                        processed_at=None,
                        error_message=None
                    )
                    item.id = db.insert(item)

                    # Generate caption
                    caption = caption_generator.generate(file_path.name)
                    logger.info(f"Generated caption: '{caption}'")

                    # Determine output path
                    output_filename = f"processed_{file_path.name}"
                    output_path = self.settings.output_dir / output_filename

                    # Process based on type
                    media_type = self.validator.get_media_type(file_path.name)
                    success = False

                    if media_type == "image":
                        success = self.processor.process_image(file_path, output_path, caption)
                    elif media_type == "video":
                        success = self.processor.process_video(file_path, output_path, caption)

                    if success:
                        # Update database
                        item.caption = caption
                        item.output_path = str(output_path)
                        item.status = MediaStatus.PROCESSED
                        item.processed_at = datetime.now()
                        db.update(item)

                        processed_count += 1
                        logger.info(f"✓ Processed: {file_path.name}")
                    else:
                        db.update_status(item.id, MediaStatus.FAILED, "Processing failed")
                        logger.error(f"✗ Failed to process: {file_path.name}")

                except Exception as e:
                    logger.error(f"Error processing {file_path.name}: {e}")
                    continue

        logger.info(f"Processed {processed_count} files")
        return processed_count

    def run_full_pipeline(
        self,
        caption_generator: CaptionGenerator,
        upload: bool = False,
        force: bool = False
    ) -> dict:
        """Run the full Drive → process → upload pipeline.

        Args:
            caption_generator: Caption generator instance
            upload: Whether to upload to YouTube (respects DRY_RUN)
            force: Force reprocessing of already-processed files

        Returns:
            Dictionary with processing statistics
        """
        stats = {
            "discovered": 0,
            "downloaded": 0,
            "processed": 0,
            "uploaded": 0,
            "failed": 0,
            "skipped": 0
        }

        logger.info("Starting full pipeline")

        # Get Drive client
        drive_client = self.get_drive_client()
        if not drive_client:
            logger.error("Cannot run pipeline without Drive access")
            return stats

        with Database(self.settings.database_path) as db:
            db.initialize()

            # List files from Drive
            all_formats = self.settings.supported_image_formats + self.settings.supported_video_formats
            files = drive_client.list_files(all_formats)
            stats["discovered"] = len(files)

            logger.info(f"Discovered {len(files)} files in Drive")

            for file_info in files:
                try:
                    file_id = file_info['id']
                    filename = file_info['name']

                    # Check if already processed
                    if not force and db.is_processed(file_id):
                        logger.info(f"Skipping already processed: {filename}")
                        stats["skipped"] += 1
                        continue

                    # Download file
                    logger.info(f"Downloading: {filename}")
                    downloaded_path = drive_client.download_file(
                        file_id,
                        filename,
                        self.settings.input_dir
                    )

                    if not downloaded_path:
                        logger.error(f"Failed to download: {filename}")
                        stats["failed"] += 1
                        continue

                    stats["downloaded"] += 1

                    # Validate
                    is_valid, error = self.validator.validate_file(downloaded_path)
                    if not is_valid:
                        logger.warning(f"Invalid file {filename}: {error}")
                        stats["skipped"] += 1
                        continue

                    # Create database entry
                    item = MediaItem(
                        id=None,
                        drive_file_id=file_id,
                        original_filename=filename,
                        source_hash=None,
                        status=MediaStatus.DOWNLOADED,
                        caption=None,
                        input_path=str(downloaded_path),
                        output_path=None,
                        youtube_video_id=None,
                        created_at=datetime.now(),
                        processed_at=None,
                        error_message=None
                    )
                    item.id = db.insert(item)

                    # Generate caption
                    caption = caption_generator.generate(filename)
                    logger.info(f"Generated caption: '{caption}'")

                    # Process media
                    output_filename = f"processed_{filename}"
                    output_path = self.settings.output_dir / output_filename

                    media_type = self.validator.get_media_type(filename)
                    success = False

                    if media_type == "image":
                        success = self.processor.process_image(downloaded_path, output_path, caption)
                    elif media_type == "video":
                        success = self.processor.process_video(downloaded_path, output_path, caption)

                    if not success:
                        db.update_status(item.id, MediaStatus.FAILED, "Processing failed")
                        stats["failed"] += 1
                        continue

                    # Update item
                    item.caption = caption
                    item.output_path = str(output_path)
                    item.status = MediaStatus.PROCESSED
                    item.processed_at = datetime.now()
                    db.update(item)

                    stats["processed"] += 1
                    logger.info(f"✓ Processed: {filename}")

                    # Upload to YouTube if requested
                    if upload and media_type == "video":
                        if self.settings.dry_run:
                            logger.info("DRY RUN: YouTube upload skipped")
                        else:
                            video_id = self._upload_to_youtube(item, output_path)
                            if video_id:
                                item.youtube_video_id = video_id
                                item.status = MediaStatus.UPLOADED
                                db.update(item)
                                stats["uploaded"] += 1

                except Exception as e:
                    logger.error(f"Error in pipeline for {file_info.get('name', 'unknown')}: {e}")
                    stats["failed"] += 1
                    continue

        logger.info(f"Pipeline complete: {stats}")
        return stats

    def _upload_to_youtube(self, item: MediaItem, video_path: Path) -> Optional[str]:
        """Upload video to YouTube.

        Args:
            item: Media item to upload
            video_path: Path to processed video

        Returns:
            YouTube video ID if successful
        """
        youtube = self.get_youtube_client()
        if not youtube:
            logger.warning("YouTube not configured, skipping upload")
            return None

        title = item.caption or item.original_filename
        description = f"Automated upload from Enlivonex YouTube Automation\n\nOriginal: {item.original_filename}"

        return youtube.upload_video(
            video_path=video_path,
            title=title,
            description=description,
            privacy_status=self.settings.youtube_privacy_status
        )
