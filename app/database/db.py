"""Database operations."""

import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from .models import (
    MediaStatus, MediaItem, ClipStatus, SourceClip,
    JobStatus, Job, JobArtifact, ArtifactType,
    YouTubeVideo, VideoAnalytics
)


class Database:
    """SQLite database manager for media processing state."""

    def __init__(self, db_path: Path):
        """Initialize database connection."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None
        self._context_depth = 0

    def connect(self):
        """Connect to the database."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        """Context manager entry."""
        if self.conn is None:
            self.connect()
        self._context_depth += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self._context_depth:
            self._context_depth -= 1
            if self._context_depth == 0:
                self.close()

    def initialize(self):
        """Create database tables if they don't exist."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()

        # Legacy media_items table (keep for compatibility)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drive_file_id TEXT NOT NULL UNIQUE,
                original_filename TEXT NOT NULL,
                source_hash TEXT,
                status TEXT NOT NULL,
                caption TEXT,
                input_path TEXT,
                output_path TEXT,
                youtube_video_id TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error_message TEXT
            )
        """)

        # Source clips library
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                aspect_ratio TEXT NOT NULL,
                orientation TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                fps REAL NOT NULL,
                codec TEXT NOT NULL,
                status TEXT NOT NULL,
                times_used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                metadata_json TEXT
            )
        """)

        # Jobs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                source_clip_id INTEGER,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT,
                config_snapshot TEXT,
                FOREIGN KEY (source_clip_id) REFERENCES source_clips(id)
            )
        """)

        # Job artifacts
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                content_text TEXT,
                file_path TEXT,
                provider TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            )
        """)

        # YouTube videos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS youtube_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                video_id TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                hashtags TEXT,
                visibility TEXT NOT NULL,
                upload_timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            )
        """)

        # Video analytics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_video_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                watch_time_minutes REAL,
                avg_view_duration_seconds REAL,
                data_source TEXT NOT NULL,
                raw_data_json TEXT,
                FOREIGN KEY (youtube_video_id) REFERENCES youtube_videos(video_id)
            )
        """)

        # Local RAG knowledge store for web research snippets and imported files.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS research_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                embedding_model TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                UNIQUE(source_id, chunk_index)
            )
        """)
        research_columns = {
            row[1]
            for row in cursor.execute(
                "PRAGMA table_info(research_chunks)"
            ).fetchall()
        }
        if "embedding_model" not in research_columns:
            cursor.execute(
                "ALTER TABLE research_chunks ADD COLUMN "
                "embedding_model TEXT NOT NULL DEFAULT 'default'"
            )

        # Daily video creation & upload logs for usage limiting
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                record_id TEXT NOT NULL,
                account_id TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                date_str TEXT NOT NULL
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_usage_date ON daily_usage_logs(date_str, action_type, account_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drive_file_id ON media_items(drive_file_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON media_items(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clip_hash ON source_clips(file_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clip_status ON source_clips(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_id ON jobs(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_status ON jobs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifact_job ON job_artifacts(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_video_id ON youtube_videos(video_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analytics_video ON video_analytics(youtube_video_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_source ON research_chunks(source_id)"
        )

        self.conn.commit()

    # Legacy methods (kept for compatibility)
    def insert(self, item: MediaItem) -> int:
        """Insert new media item (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO media_items (
                drive_file_id, original_filename, source_hash, status,
                caption, input_path, output_path, youtube_video_id,
                created_at, processed_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.drive_file_id, item.original_filename, item.source_hash,
            item.status.value, item.caption, item.input_path, item.output_path,
            item.youtube_video_id, item.created_at.isoformat(),
            item.processed_at.isoformat() if item.processed_at else None,
            item.error_message,
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_by_id(self, item_id: int) -> Optional[MediaItem]:
        """Retrieve media item by ID (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM media_items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return MediaItem.from_row(tuple(row)) if row else None

    def get_by_drive_file_id(self, drive_file_id: str) -> Optional[MediaItem]:
        """Retrieve media item by Google Drive file ID."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM media_items WHERE drive_file_id = ?", (drive_file_id,))
        row = cursor.fetchone()
        return MediaItem.from_row(tuple(row)) if row else None

    def update(self, item: MediaItem):
        """Update existing media item (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        if not item.id:
            raise ValueError("Cannot update item without ID")

        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE media_items SET
                drive_file_id = ?, original_filename = ?, source_hash = ?,
                status = ?, caption = ?, input_path = ?, output_path = ?,
                youtube_video_id = ?, created_at = ?, processed_at = ?, error_message = ?
            WHERE id = ?
        """, (
            item.drive_file_id, item.original_filename, item.source_hash,
            item.status.value, item.caption, item.input_path, item.output_path,
            item.youtube_video_id, item.created_at.isoformat(),
            item.processed_at.isoformat() if item.processed_at else None,
            item.error_message, item.id,
        ))
        self.conn.commit()

    def update_status(self, item_id: int, status: MediaStatus, error_message: Optional[str] = None):
        """Update status of a media item (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        processed_at = datetime.now().isoformat() if status in (
            MediaStatus.PROCESSED, MediaStatus.UPLOADED, MediaStatus.FAILED
        ) else None

        cursor.execute("""
            UPDATE media_items
            SET status = ?, processed_at = ?, error_message = ?
            WHERE id = ?
        """, (status.value, processed_at, error_message, item_id))
        self.conn.commit()

    def get_by_status(self, status: MediaStatus) -> List[MediaItem]:
        """Retrieve all media items with given status (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM media_items WHERE status = ? ORDER BY created_at DESC",
            (status.value,)
        )
        return [MediaItem.from_row(tuple(row)) for row in cursor.fetchall()]

    def count_by_status(self, status: MediaStatus) -> int:
        """Count items with given status (legacy)."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM media_items WHERE status = ?",
            (status.value,)
        )
        return cursor.fetchone()[0]

    def is_processed(self, drive_file_id: str) -> bool:
        """Check if a Drive file has already been processed."""
        item = self.get_by_drive_file_id(drive_file_id)
        return item is not None

    # New methods for clips
    def insert_clip(self, clip: SourceClip) -> int:
        """Insert new source clip."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO source_clips (
                filename, file_hash, file_path, duration_seconds,
                width, height, aspect_ratio, orientation,
                file_size_bytes, fps, codec, status, times_used,
                created_at, last_used_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            clip.filename, clip.file_hash, clip.file_path,
            clip.duration_seconds, clip.width, clip.height,
            clip.aspect_ratio, clip.orientation, clip.file_size_bytes,
            clip.fps, clip.codec, clip.status.value, clip.times_used,
            clip.created_at.isoformat(),
            clip.last_used_at.isoformat() if clip.last_used_at else None,
            clip.metadata_json
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_clip_by_hash(self, file_hash: str) -> Optional[SourceClip]:
        """Get clip by file hash."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM source_clips WHERE file_hash = ?", (file_hash,))
        row = cursor.fetchone()
        if not row:
            return None

        return SourceClip(
            id=row[0], filename=row[1], file_hash=row[2], file_path=row[3],
            duration_seconds=row[4], width=row[5], height=row[6],
            aspect_ratio=row[7], orientation=row[8], file_size_bytes=row[9],
            fps=row[10], codec=row[11], status=ClipStatus(row[12]),
            times_used=row[13], created_at=datetime.fromisoformat(row[14]),
            last_used_at=datetime.fromisoformat(row[15]) if row[15] else None,
            metadata_json=row[16]
        )

    def update_clip_location(
        self, clip_id: int, filename: str, file_path: str, file_size_bytes: int
    ) -> None:
        """Update a clip's path after it has been staged in the managed library."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            """
            UPDATE source_clips
            SET filename = ?, file_path = ?, file_size_bytes = ?
            WHERE id = ?
            """,
            (filename, file_path, file_size_bytes, clip_id),
        )
        self.conn.commit()

    def get_unused_clips(self, limit: int = 100) -> List[SourceClip]:
        """Get unused clips."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM source_clips
            WHERE status = ? AND times_used = 0
            ORDER BY created_at DESC LIMIT ?
        """, (ClipStatus.AVAILABLE.value, limit))

        clips = []
        for row in cursor.fetchall():
            clips.append(SourceClip(
                id=row[0], filename=row[1], file_hash=row[2], file_path=row[3],
                duration_seconds=row[4], width=row[5], height=row[6],
                aspect_ratio=row[7], orientation=row[8], file_size_bytes=row[9],
                fps=row[10], codec=row[11], status=ClipStatus(row[12]),
                times_used=row[13], created_at=datetime.fromisoformat(row[14]),
                last_used_at=datetime.fromisoformat(row[15]) if row[15] else None,
                metadata_json=row[16]
            ))
        return clips

    def get_available_clips(self, limit: int = 100) -> List[SourceClip]:
        """Get available clips, including previously used clips."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM source_clips
            WHERE status = ?
            ORDER BY times_used ASC, created_at ASC
            LIMIT ?
        """, (ClipStatus.AVAILABLE.value, limit))

        return [
            SourceClip(
                id=row[0], filename=row[1], file_hash=row[2], file_path=row[3],
                duration_seconds=row[4], width=row[5], height=row[6],
                aspect_ratio=row[7], orientation=row[8], file_size_bytes=row[9],
                fps=row[10], codec=row[11], status=ClipStatus(row[12]),
                times_used=row[13], created_at=datetime.fromisoformat(row[14]),
                last_used_at=datetime.fromisoformat(row[15]) if row[15] else None,
                metadata_json=row[16]
            )
            for row in cursor.fetchall()
        ]

    def insert_job(self, job: Job) -> int:
        """Insert new job."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO jobs (
                job_id, source_clip_id, status, current_stage,
                started_at, completed_at, error_message, config_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.job_id, job.source_clip_id, job.status.value,
            job.current_stage, job.started_at.isoformat(),
            job.completed_at.isoformat() if job.completed_at else None,
            job.error_message, job.config_snapshot
        ))
        self.conn.commit()
        return cursor.lastrowid

    def update_job_status(self, job_id: str, status: JobStatus, stage: str, error: Optional[str] = None):
        """Update job status."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            cursor.execute("""
                UPDATE jobs SET status = ?, current_stage = ?,
                completed_at = ?, error_message = ?
                WHERE job_id = ?
            """, (status.value, stage, datetime.now().isoformat(), error, job_id))
        else:
            cursor.execute("""
                UPDATE jobs SET status = ?, current_stage = ?, error_message = ?
                WHERE job_id = ?
            """, (status.value, stage, error, job_id))
        self.conn.commit()

    @staticmethod
    def hash_file(file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
