"""Clip library manager for ingesting and tracking local video clips."""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from dataclasses import asdict

from ..database.db import Database
from ..database.models import SourceClip, ClipStatus
from ..utils.ffmpeg import FFmpegWrapper, VideoInfo


class ClipManager:
    """Manages local video clip library."""

    def __init__(self, db: Database, clips_dir: Path, ffmpeg: Optional[FFmpegWrapper] = None):
        """Initialize clip manager.

        Args:
            db: Database instance
            clips_dir: Directory containing clip files
            ffmpeg: FFmpeg wrapper (creates default if not provided)
        """
        self.db = db
        self.clips_dir = clips_dir
        self.ffmpeg = ffmpeg or FFmpegWrapper()

        # Ensure clips directory exists
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    def ingest_clip(self, file_path: Path, metadata: Optional[dict] = None) -> SourceClip:
        """Ingest a video clip into the library.

        Args:
            file_path: Path to video file
            metadata: Optional metadata dictionary

        Returns:
            SourceClip object

        Raises:
            RuntimeError: If FFmpeg not available or file invalid
            FileNotFoundError: If file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not self.ffmpeg.is_available():
            raise RuntimeError("FFmpeg not available - cannot probe video")

        # Compute file hash
        file_hash = Database.hash_file(file_path)

        # Check for duplicate
        existing = self.db.get_clip_by_hash(file_hash)
        if existing:
            return existing

        # Probe video
        info = self.ffmpeg.probe(file_path)

        # Create clip record
        clip = SourceClip(
            id=None,
            filename=file_path.name,
            file_hash=file_hash,
            file_path=str(file_path.absolute()),
            duration_seconds=info.duration,
            width=info.width,
            height=info.height,
            aspect_ratio=info.aspect_ratio,
            orientation=info.orientation,
            file_size_bytes=file_path.stat().st_size,
            fps=info.fps,
            codec=info.codec,
            status=ClipStatus.AVAILABLE,
            times_used=0,
            created_at=datetime.now(),
            last_used_at=None,
            metadata_json=json.dumps(metadata) if metadata else None
        )

        # Insert into database
        clip.id = self.db.insert_clip(clip)

        return clip

    def ingest_directory(self, directory: Optional[Path] = None, recursive: bool = False) -> List[SourceClip]:
        """Ingest all video files from a directory.

        Args:
            directory: Directory to scan (defaults to clips_dir)
            recursive: Whether to scan subdirectories

        Returns:
            List of ingested clips (excludes duplicates)
        """
        scan_dir = directory or self.clips_dir

        if not scan_dir.exists():
            raise FileNotFoundError(f"Directory not found: {scan_dir}")

        video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv'}
        pattern = "**/*" if recursive else "*"

        ingested = []
        skipped = []
        errors = []

        for file_path in scan_dir.glob(pattern):
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() not in video_extensions:
                continue

            try:
                clip = self.ingest_clip(file_path)
                if clip.created_at == datetime.now() or (datetime.now() - clip.created_at).seconds < 2:
                    ingested.append(clip)
                else:
                    skipped.append(file_path.name)
            except Exception as e:
                errors.append((file_path.name, str(e)))

        return ingested

    def get_unused_clips(self, limit: int = 100) -> List[SourceClip]:
        """Get clips that haven't been used yet.

        Args:
            limit: Maximum number of clips to return

        Returns:
            List of unused clips
        """
        return self.db.get_unused_clips(limit)

    def get_available_clips(self, limit: int = 100) -> List[SourceClip]:
        """Get all available clips, ranked later by the configured selector."""
        return self.db.get_available_clips(limit)

    def get_clip_by_hash(self, file_hash: str) -> Optional[SourceClip]:
        """Get clip by file hash.

        Args:
            file_hash: SHA256 hash of file

        Returns:
            SourceClip or None
        """
        return self.db.get_clip_by_hash(file_hash)

    def mark_clip_used(self, clip_id: int):
        """Mark a clip as used (increment usage counter).

        Args:
            clip_id: Clip ID
        """
        if not self.db.conn:
            raise RuntimeError("Database not connected")

        cursor = self.db.conn.cursor()
        cursor.execute("""
            UPDATE source_clips
            SET times_used = times_used + 1, last_used_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), clip_id))
        self.db.conn.commit()

    def get_clip_stats(self) -> dict:
        """Get clip library statistics.

        Returns:
            Dictionary with stats
        """
        if not self.db.conn:
            raise RuntimeError("Database not connected")

        cursor = self.db.conn.cursor()

        # Total clips
        cursor.execute("SELECT COUNT(*) FROM source_clips")
        total = cursor.fetchone()[0]

        # By status
        cursor.execute("""
            SELECT status, COUNT(*) FROM source_clips
            GROUP BY status
        """)
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # Unused clips
        cursor.execute("SELECT COUNT(*) FROM source_clips WHERE times_used = 0")
        unused = cursor.fetchone()[0]

        # Total duration
        cursor.execute("SELECT SUM(duration_seconds) FROM source_clips WHERE status = ?",
                      (ClipStatus.AVAILABLE.value,))
        total_duration = cursor.fetchone()[0] or 0.0

        # By orientation
        cursor.execute("""
            SELECT orientation, COUNT(*) FROM source_clips
            WHERE status = ?
            GROUP BY orientation
        """, (ClipStatus.AVAILABLE.value,))
        by_orientation = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total": total,
            "unused": unused,
            "by_status": by_status,
            "by_orientation": by_orientation,
            "total_duration_seconds": total_duration,
            "total_duration_minutes": round(total_duration / 60, 2)
        }
