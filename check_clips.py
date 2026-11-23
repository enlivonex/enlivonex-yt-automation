#!/usr/bin/env python
"""Check available clips and prepare for smoke test."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.database.db import Database

DB_PATH = Path("data/media.db")

if not DB_PATH.exists():
    print(f"✗ Database not found at {DB_PATH}")
    sys.exit(1)

with Database(DB_PATH) as db:
    db.initialize()
    cursor = db.conn.cursor()

    # Check for source clips
    cursor.execute("SELECT COUNT(*) FROM source_clips")
    clip_count = cursor.fetchone()[0]
    print(f"Source clips in database: {clip_count}")

    if clip_count > 0:
        cursor.execute("""
            SELECT id, filename, duration_seconds, orientation, status, times_used
            FROM source_clips
            ORDER BY times_used ASC
            LIMIT 5
        """)
        print("\nAvailable clips:")
        for row in cursor.fetchall():
            print(f"  ID={row[0]}, {row[1]}, {row[2]:.1f}s, {row[3]}, status={row[4]}, used={row[5]}x")

    # Check for jobs
    cursor.execute("SELECT COUNT(*) FROM jobs")
    job_count = cursor.fetchone()[0]
    print(f"\nJobs in database: {job_count}")

    if job_count > 0:
        cursor.execute("""
            SELECT job_id, status, current_stage
            FROM jobs
            ORDER BY started_at DESC
            LIMIT 3
        """)
        print("\nRecent jobs:")
        for row in cursor.fetchall():
            print(f"  {row[0]}: {row[1]} ({row[2]})")

print("\n✓ Database check complete")
