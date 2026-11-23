"""Daily video generation and upload limit enforcement with Administrator bypass."""

from datetime import datetime, date
import logging
from pathlib import Path
import sqlite3
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

ADMIN_KEY: str = "ENLSKK2008"
DAILY_VIDEO_LIMIT: int = 2


class DailyLimitExceededError(RuntimeError):
    """Raised when the 2 videos per day limit is exceeded without an Admin key."""
    pass


def verify_admin_key(key: Optional[str]) -> bool:
    """Verify if the provided key matches the Administrator Key."""
    if not key or not isinstance(key, str):
        return False
    return key.strip() == ADMIN_KEY


def is_admin_mode(settings: Optional[Any] = None, admin_key: Optional[str] = None) -> bool:
    """Check if Administrator Mode is active."""
    if verify_admin_key(admin_key):
        return True
    if settings is not None:
        key_from_settings = getattr(settings, "admin_key", None)
        if verify_admin_key(key_from_settings):
            return True
    return False


def _get_connection(database_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_usage_tables(database_path: Path) -> None:
    """Ensure the daily usage logs table exists."""
    conn = _get_connection(database_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    account_id TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    date_str TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_usage_date
                ON daily_usage_logs(date_str, action_type, account_id)
            """)
    finally:
        conn.close()


def record_video_creation(database_path: Path, job_id: str) -> None:
    """Record a completed video creation for today."""
    ensure_usage_tables(database_path)
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    conn = _get_connection(database_path)
    try:
        with conn:
            conn.execute("""
                INSERT INTO daily_usage_logs (action_type, record_id, created_at, date_str)
                VALUES (?, ?, ?, ?)
            """, ("creation", job_id, now.isoformat(), today_str))
    finally:
        conn.close()


def record_video_upload(database_path: Path, video_id: str, account_id: Optional[str] = None) -> None:
    """Record a completed video upload for today."""
    ensure_usage_tables(database_path)
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    conn = _get_connection(database_path)
    try:
        with conn:
            conn.execute("""
                INSERT INTO daily_usage_logs (action_type, record_id, account_id, created_at, date_str)
                VALUES (?, ?, ?, ?, ?)
            """, ("upload", video_id, account_id or "default", now.isoformat(), today_str))
    finally:
        conn.close()


def get_daily_creation_count(database_path: Path, target_date: Optional[date] = None) -> int:
    """Return the total number of videos made today."""
    ensure_usage_tables(database_path)
    target_date_str = (target_date or date.today()).strftime("%Y-%m-%d")
    conn = _get_connection(database_path)
    try:
        row = conn.execute("""
            SELECT COUNT(DISTINCT record_id) AS cnt
            FROM daily_usage_logs
            WHERE action_type = 'creation' AND date_str = ?
        """, (target_date_str,)).fetchone()
        log_count = row["cnt"] if row else 0

        # Also count completed jobs in `jobs` table starting today if it exists
        jobs_count = 0
        try:
            row_jobs = conn.execute("""
                SELECT COUNT(*) AS cnt
                FROM jobs
                WHERE status = 'COMPLETED'
                  AND started_at LIKE ?
            """, (f"{target_date_str}%",)).fetchone()
            jobs_count = row_jobs["cnt"] if row_jobs else 0
        except sqlite3.OperationalError:
            pass  # jobs table may not exist in test databases

        return max(log_count, jobs_count)
    finally:
        conn.close()


def get_daily_upload_count(
    database_path: Path, account_id: Optional[str] = None, target_date: Optional[date] = None
) -> int:
    """Return the total number of uploads today (optionally for a specific YouTube account)."""
    ensure_usage_tables(database_path)
    target_date_str = (target_date or date.today()).strftime("%Y-%m-%d")
    conn = _get_connection(database_path)
    try:
        if account_id:
            row = conn.execute("""
                SELECT COUNT(DISTINCT record_id) AS cnt
                FROM daily_usage_logs
                WHERE action_type = 'upload' AND date_str = ? AND account_id = ?
            """, (target_date_str, account_id)).fetchone()
            log_count = row["cnt"] if row else 0
            return log_count
        else:
            row = conn.execute("""
                SELECT COUNT(DISTINCT record_id) AS cnt
                FROM daily_usage_logs
                WHERE action_type = 'upload' AND date_str = ?
            """, (target_date_str,)).fetchone()
            log_count = row["cnt"] if row else 0

            row_yt = conn.execute("""
                SELECT COUNT(*) AS cnt
                FROM youtube_videos
                WHERE upload_timestamp LIKE ?
            """, (f"{target_date_str}%",)).fetchone()
            yt_count = row_yt["cnt"] if row_yt else 0

            return max(log_count, yt_count)
    finally:
        conn.close()


def check_creation_limit(
    database_path: Path,
    admin_key: Optional[str] = None,
    settings: Optional[Any] = None,
    target_date: Optional[date] = None,
) -> Tuple[bool, str, int]:
    """Check if the user is allowed to create another video today.

    Returns:
        (is_allowed, status_message, current_count)
    """
    current_count = get_daily_creation_count(database_path, target_date=target_date)
    if is_admin_mode(settings=settings, admin_key=admin_key):
        return (
            True,
            f"Administrator mode active (Unlimited creations, {current_count} made today).",
            current_count,
        )

    if current_count >= DAILY_VIDEO_LIMIT:
        message = (
            f"Daily video limit reached: You have created {current_count}/{DAILY_VIDEO_LIMIT} "
            f"videos today. In this testing phase, accounts are limited to 2 videos per day.\n\n"
            f"Enter the Administrator Key to unlock unlimited video creation."
        )
        return False, message, current_count

    remaining = DAILY_VIDEO_LIMIT - current_count
    return (
        True,
        f"Daily limit: {current_count}/{DAILY_VIDEO_LIMIT} videos created today ({remaining} remaining).",
        current_count,
    )


def check_upload_limit(
    database_path: Path,
    account_id: Optional[str] = None,
    admin_key: Optional[str] = None,
    settings: Optional[Any] = None,
    target_date: Optional[date] = None,
) -> Tuple[bool, str, int]:
    """Check if the user is allowed to upload another video today for this account.

    Returns:
        (is_allowed, status_message, current_count)
    """
    current_count = get_daily_upload_count(
        database_path, account_id=account_id, target_date=target_date
    )
    if is_admin_mode(settings=settings, admin_key=admin_key):
        return (
            True,
            f"Administrator mode active (Unlimited uploads, {current_count} uploaded today).",
            current_count,
        )

    if current_count >= DAILY_VIDEO_LIMIT:
        message = (
            f"Daily upload limit reached: {current_count}/{DAILY_VIDEO_LIMIT} videos "
            f"have been uploaded today for this account. In this testing phase, accounts are "
            f"limited to 2 videos per day.\n\n"
            f"Enter the Administrator Key to unlock unlimited uploads."
        )
        return False, message, current_count

    remaining = DAILY_VIDEO_LIMIT - current_count
    return (
        True,
        f"Daily upload limit: {current_count}/{DAILY_VIDEO_LIMIT} uploaded today ({remaining} remaining).",
        current_count,
    )
