"""Database models for Enlivonex YouTube Automation."""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class MediaStatus(Enum):
    """Legacy status - kept for compatibility."""
    DISCOVERED = "DISCOVERED"
    DOWNLOADED = "DOWNLOADED"
    PROCESSED = "PROCESSED"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ClipStatus(Enum):
    """Status of a source clip."""
    AVAILABLE = "AVAILABLE"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class JobStatus(Enum):
    """Status of an automation job."""
    QUEUED = "QUEUED"
    SELECTING_CLIP = "SELECTING_CLIP"
    ANALYZING_CLIP = "ANALYZING_CLIP"
    GENERATING_STORY = "GENERATING_STORY"
    RESEARCHING = "RESEARCHING"
    GENERATING_VOICE = "GENERATING_VOICE"
    GENERATING_CAPTIONS = "GENERATING_CAPTIONS"
    RENDERING = "RENDERING"
    GENERATING_METADATA = "GENERATING_METADATA"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ArtifactType(Enum):
    """Type of job artifact."""
    STORY = "STORY"
    RESEARCH = "RESEARCH"
    VOICEOVER = "VOICEOVER"
    CAPTIONS = "CAPTIONS"
    VIDEO = "VIDEO"
    METADATA = "METADATA"
    THUMBNAIL = "THUMBNAIL"


@dataclass
class MediaItem:
    """Legacy media item - kept for compatibility."""
    id: Optional[int]
    drive_file_id: str
    original_filename: str
    source_hash: Optional[str]
    status: MediaStatus
    caption: Optional[str]
    input_path: Optional[str]
    output_path: Optional[str]
    youtube_video_id: Optional[str]
    created_at: datetime
    processed_at: Optional[datetime]
    error_message: Optional[str]

    @classmethod
    def from_row(cls, row: tuple) -> "MediaItem":
        """Create MediaItem from database row."""
        return cls(
            id=row[0],
            drive_file_id=row[1],
            original_filename=row[2],
            source_hash=row[3],
            status=MediaStatus(row[4]),
            caption=row[5],
            input_path=row[6],
            output_path=row[7],
            youtube_video_id=row[8],
            created_at=datetime.fromisoformat(row[9]),
            processed_at=datetime.fromisoformat(row[10]) if row[10] else None,
            error_message=row[11],
        )


@dataclass
class SourceClip:
    """Source video clip from local library."""
    id: Optional[int]
    filename: str
    file_hash: str
    file_path: str
    duration_seconds: float
    width: int
    height: int
    aspect_ratio: str
    orientation: str  # portrait, landscape, square
    file_size_bytes: int
    fps: float
    codec: str
    status: ClipStatus
    times_used: int
    created_at: datetime
    last_used_at: Optional[datetime]
    metadata_json: Optional[str]


@dataclass
class Job:
    """Automation job."""
    id: Optional[int]
    job_id: str  # JOB-2026-XXXXXX format
    source_clip_id: Optional[int]
    status: JobStatus
    current_stage: str
    started_at: datetime
    completed_at: Optional[datetime]
    error_message: Optional[str]
    config_snapshot: Optional[str]  # JSON


@dataclass
class JobArtifact:
    """Generated content artifact."""
    id: Optional[int]
    job_id: str
    artifact_type: ArtifactType
    content_text: Optional[str]
    file_path: Optional[str]
    provider: Optional[str]
    metadata_json: Optional[str]
    created_at: datetime


@dataclass
class YouTubeVideo:
    """Uploaded YouTube video."""
    id: Optional[int]
    job_id: str
    video_id: str  # YouTube video ID
    url: str
    title: str
    description: str
    hashtags: str
    visibility: str  # private, unlisted, public
    upload_timestamp: datetime
    status: str


@dataclass
class VideoAnalytics:
    """YouTube video analytics snapshot."""
    id: Optional[int]
    youtube_video_id: str
    fetched_at: datetime
    views: int
    likes: int
    comments: int
    watch_time_minutes: Optional[float]
    avg_view_duration_seconds: Optional[float]
    data_source: str  # youtube_api, manual, etc.
    raw_data_json: Optional[str]
