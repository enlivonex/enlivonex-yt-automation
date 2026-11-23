"""Shared values and small helpers for desktop interfaces."""

from datetime import datetime
from pathlib import Path
import uuid

ACTIVITY_MESSAGES = (
    "Cooking up your video...",
    "Bringing the story to life...",
    "Making a little video magic...",
    "The system is running at full potential...",
    "Composing the scenes...",
    "Adding the finishing touches...",
)
JOB_PROGRESS = {
    "QUEUED": 0,
    "SELECTING_CLIP": 12,
    "RESEARCHING": 24,
    "GENERATING_STORY": 30,
    "GENERATING_VOICE": 50,
    "GENERATING_CAPTIONS": 68,
    "RENDERING": 84,
    "GENERATING_METADATA": 96,
    "COMPLETED": 100,
}


def upload_job_id(video_path: Path) -> str:
    """Reuse a generated job ID or create a unique ID for external MP4s."""
    if video_path.parent.name.startswith("JOB-"):
        return video_path.parent.name
    return f"UPLOAD-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
