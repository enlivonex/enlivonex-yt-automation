"""Flask dashboard for Enlivonex YouTube Automation."""

import ipaddress
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, jsonify, request

from ..database.db import Database
from ..database.models import JobStatus, ClipStatus


def validate_dashboard_host(host: str) -> str:
    """Accept only explicit loopback hosts for the unauthenticated local dashboard."""
    normalized_host = host.strip().lower()
    if normalized_host == "localhost":
        return normalized_host
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError as error:
        raise ValueError(
            "The unauthenticated dashboard may bind only to localhost or a "
            "loopback IP address."
        ) from error
    if not address.is_loopback:
        raise ValueError(
            "The unauthenticated dashboard may bind only to localhost or a "
            "loopback IP address."
        )
    return normalized_host


def create_app(db_path: Path, clips_dir: Path, jobs_dir: Path) -> Flask:
    """Create Flask application.

    Args:
        db_path: Path to database
        clips_dir: Path to clips directory
        jobs_dir: Path to jobs directory

    Returns:
        Flask app instance
    """
    app = Flask(__name__, static_folder='static', template_folder='templates')

    db = Database(db_path)

    @app.route('/')
    def index():
        """Render dashboard homepage."""
        return render_template('index.html')

    @app.route('/api/status')
    def get_status():
        """Get current automation status."""
        with db:
            cursor = db.conn.cursor()

            # Get latest job
            cursor.execute("""
                SELECT job_id, status, current_stage, started_at, completed_at
                FROM jobs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            job_row = cursor.fetchone()

            # Count clips by status
            cursor.execute("""
                SELECT status, COUNT(*) FROM source_clips
                GROUP BY status
            """)
            clips_by_status = {row[0]: row[1] for row in cursor.fetchall()}

            # Count jobs by status
            cursor.execute("""
                SELECT status, COUNT(*) FROM jobs
                GROUP BY status
            """)
            jobs_by_status = {row[0]: row[1] for row in cursor.fetchall()}

            # Count uploaded videos
            cursor.execute("SELECT COUNT(*) FROM youtube_videos")
            uploaded_count = cursor.fetchone()[0]

        status = {
            'current_job': None,
            'clips': clips_by_status,
            'jobs': jobs_by_status,
            'uploaded_videos': uploaded_count,
            'timestamp': datetime.now().isoformat()
        }

        if job_row:
            status['current_job'] = {
                'job_id': job_row[0],
                'status': job_row[1],
                'stage': job_row[2],
                'started_at': job_row[3],
                'completed_at': job_row[4]
            }

        return jsonify(status)

    @app.route('/api/clips')
    def get_clips():
        """Get clips list."""
        limit = request.args.get('limit', 50, type=int)
        status = request.args.get('status', None)

        with db:
            cursor = db.conn.cursor()

            if status:
                cursor.execute("""
                    SELECT id, filename, duration_seconds, orientation,
                           times_used, status, created_at
                    FROM source_clips
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (status, limit))
            else:
                cursor.execute("""
                    SELECT id, filename, duration_seconds, orientation,
                           times_used, status, created_at
                    FROM source_clips
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))

            clips = []
            for row in cursor.fetchall():
                clips.append({
                    'id': row[0],
                    'filename': row[1],
                    'duration': row[2],
                    'orientation': row[3],
                    'times_used': row[4],
                    'status': row[5],
                    'created_at': row[6]
                })

        return jsonify(clips)

    @app.route('/api/jobs')
    def get_jobs():
        """Get jobs list."""
        limit = request.args.get('limit', 20, type=int)

        with db:
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT job_id, status, current_stage, started_at,
                       completed_at, error_message
                FROM jobs
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))

            jobs = []
            for row in cursor.fetchall():
                jobs.append({
                    'job_id': row[0],
                    'status': row[1],
                    'stage': row[2],
                    'started_at': row[3],
                    'completed_at': row[4],
                    'error': row[5]
                })

        return jsonify(jobs)

    @app.route('/api/jobs/<job_id>')
    def get_job_details(job_id):
        """Get job details."""
        with db:
            cursor = db.conn.cursor()

            # Get job
            cursor.execute("""
                SELECT * FROM jobs WHERE job_id = ?
            """, (job_id,))
            job_row = cursor.fetchone()

            if not job_row:
                return jsonify({'error': 'Job not found'}), 404

            # Get artifacts
            cursor.execute("""
                SELECT artifact_type, file_path, created_at
                FROM job_artifacts
                WHERE job_id = ?
                ORDER BY created_at ASC
            """, (job_id,))
            artifacts = []
            for row in cursor.fetchall():
                artifacts.append({
                    'type': row[0],
                    'file_path': row[1],
                    'created_at': row[2]
                })

            # Get uploaded video if exists
            cursor.execute("""
                SELECT video_id, url, title, visibility, upload_timestamp
                FROM youtube_videos
                WHERE job_id = ?
            """, (job_id,))
            video_row = cursor.fetchone()

            video = None
            if video_row:
                video = {
                    'video_id': video_row[0],
                    'url': video_row[1],
                    'title': video_row[2],
                    'visibility': video_row[3],
                    'uploaded_at': video_row[4]
                }

        job = {
            'job_id': job_row[1],
            'status': job_row[3],
            'stage': job_row[4],
            'started_at': job_row[5],
            'completed_at': job_row[6],
            'error': job_row[7],
            'artifacts': artifacts,
            'video': video
        }

        return jsonify(job)

    @app.route('/api/analytics')
    def get_analytics():
        """Get analytics for uploaded videos."""
        with db:
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT v.video_id, v.title, v.url,
                       a.views, a.likes, a.comments, a.fetched_at
                FROM youtube_videos v
                LEFT JOIN video_analytics a ON v.video_id = a.youtube_video_id
                WHERE a.id IN (
                    SELECT MAX(id) FROM video_analytics
                    GROUP BY youtube_video_id
                )
                ORDER BY a.views DESC
                LIMIT 50
            """)

            analytics = []
            for row in cursor.fetchall():
                analytics.append({
                    'video_id': row[0],
                    'title': row[1],
                    'url': row[2],
                    'views': row[3] or 0,
                    'likes': row[4] or 0,
                    'comments': row[5] or 0,
                    'fetched_at': row[6]
                })

        return jsonify(analytics)

    return app


def run_dashboard(
    db_path: Path,
    clips_dir: Path,
    jobs_dir: Path,
    host: str = '127.0.0.1',
    port: int = 5000,
    debug: bool = False
):
    """Run the Flask dashboard.

    Args:
        db_path: Path to database
        clips_dir: Path to clips directory
        jobs_dir: Path to jobs directory
        host: Host to bind to
        port: Port to bind to
        debug: Enable debug mode
    """
    host = validate_dashboard_host(host)
    app = create_app(db_path, clips_dir, jobs_dir)
    app.run(host=host, port=port, debug=debug)
