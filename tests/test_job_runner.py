"""Tests for the shared CLI/GUI local job runner."""

from datetime import datetime
import os
from pathlib import Path
from types import SimpleNamespace

from app.clips.selector import CompositeSelector, LeastUsedSelector
from app.database.db import Database
from app.orchestration import runner as runner_module
from app.orchestration.runner import LocalJobRunner
from app.utils.ffmpeg import VideoInfo


def test_selector_uses_existing_clip_preference_composition():
    selector = LocalJobRunner.create_selector(None)

    assert isinstance(selector, CompositeSelector)
    assert [type(item) for item in selector.selectors] == [LeastUsedSelector]


def test_selector_preserves_orientation_as_a_preference():
    selector = LocalJobRunner.create_selector("portrait")

    assert [type(item).__name__ for item in selector.selectors] == [
        "OrientationSelector",
        "LeastUsedSelector",
    ]


def test_runner_creates_job_with_existing_providers_and_returns_video_path(
    monkeypatch, tmp_path
):
    database_path = tmp_path / "automation.db"
    clips_dir = tmp_path / "clips"
    jobs_dir = tmp_path / "jobs"
    db = Database(database_path)
    clip_manager = SimpleNamespace(db=db)
    story_provider = object()
    voice_provider = object()
    voice_config = object()
    monkeypatch.setattr(
        runner_module, "load_settings", lambda: SimpleNamespace(story_provider="mock")
    )
    monkeypatch.setattr(
        runner_module,
        "create_ai_providers",
        lambda settings, ollama_client=None: SimpleNamespace(
            story=story_provider,
            voice=voice_provider,
            voice_config=voice_config,
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "create_metadata_generator",
        lambda settings, ollama_client=None: object(),
    )
    monkeypatch.setattr(
        runner_module, "ClipManager", lambda _db, _clips_dir: clip_manager
    )

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            assert kwargs["db"] is db
            assert kwargs["clip_manager"] is clip_manager
            assert kwargs["story_provider"] is story_provider
            assert kwargs["voice_provider"] is voice_provider

        def create_job(self, clip_selector, config):
            assert isinstance(clip_selector, CompositeSelector)
            assert config == {
                "story_duration": 30,
                "story_niche": "fitness",
                "output_orientation": "portrait",
            }
            return "JOB-TEST-123456"

        def run_job(self, job_id, **kwargs):
            assert job_id == "JOB-TEST-123456"
            assert kwargs["voice_config"] is voice_config
            assert kwargs["composition_config"].width == 1080
            assert kwargs["composition_config"].height == 1920
            assert kwargs["composition_config"].target_duration is None
            assert kwargs["story_duration"] == 30
            assert kwargs["story_niche"] == "fitness"
            output = jobs_dir / job_id / "output.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"generated video")
            return {
                "job_id": job_id,
                "status": "completed",
                "clip": "source.mp4",
                "clips": ["source.mp4"],
                "story_title": "Test story",
                "artifacts_dir": str(output.parent),
            }

    monkeypatch.setattr(runner_module, "JobOrchestrator", FakeOrchestrator)
    announced_jobs = []
    runner = LocalJobRunner(database_path, clips_dir, jobs_dir)

    result = runner.run_job(
        on_job_created=announced_jobs.append,
        orientation="portrait",
        story_duration=30,
        story_niche="fitness",
    )

    assert announced_jobs == ["JOB-TEST-123456"]
    assert result["output_path"] == str(
        jobs_dir / "JOB-TEST-123456" / "output.mp4"
    )


def test_stage_lookup_reads_latest_persisted_stage(tmp_path):
    database_path = tmp_path / "automation.db"
    with Database(database_path) as db:
        db.initialize()
        db.conn.execute(
            """
            INSERT INTO jobs (
                job_id, source_clip_id, status, current_stage, started_at,
                completed_at, error_message, config_snapshot
            ) VALUES (?, NULL, ?, ?, ?, NULL, NULL, '{}')
            """,
            ("JOB-TEST-123456", "GENERATING_VOICE", "GENERATING_VOICE",
             datetime.now().isoformat()),
        )
        db.conn.commit()

    assert LocalJobRunner(database_path).get_job_stage("JOB-TEST-123456") == (
        "GENERATING_VOICE"
    )
    assert LocalJobRunner(database_path).get_job_stage("missing") is None


def test_recent_outputs_returns_nonempty_job_outputs_newest_first(tmp_path):
    jobs_dir = tmp_path / "jobs"
    older = jobs_dir / "JOB-OLDER" / "output.mp4"
    newer = jobs_dir / "JOB-NEWER" / "output.mp4"
    empty = jobs_dir / "JOB-EMPTY" / "output.mp4"
    unrelated = jobs_dir / "JOB-OTHER" / "preview.mp4"
    for path in (older, newer, empty, unrelated):
        path.parent.mkdir(parents=True, exist_ok=True)
    older.write_bytes(b"older video")
    newer.write_bytes(b"newer video")
    empty.touch()
    unrelated.write_bytes(b"not a job output")
    older.touch()
    newer.touch()
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    outputs = LocalJobRunner(jobs_dir=jobs_dir).get_recent_outputs()

    assert outputs == [newer, older]
    assert LocalJobRunner(jobs_dir=jobs_dir).get_recent_outputs(limit=1) == [
        newer
    ]


def test_pruning_keeps_ten_recent_completed_job_folders_and_preserves_records(
    tmp_path,
):
    database_path = tmp_path / "automation.db"
    jobs_dir = tmp_path / "jobs"
    runner = LocalJobRunner(database_path=database_path, jobs_dir=jobs_dir)
    with Database(database_path) as db:
        db.initialize()
        for index in range(12):
            job_id = f"JOB-GENERATED-{index:02d}"
            output = jobs_dir / job_id / "output.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"generated video")
            (output.parent / "story.json").write_text("{}", encoding="utf-8")
            os.utime(output, (100 + index, 100 + index))
            db.conn.execute(
                """
                INSERT INTO jobs (
                    job_id, source_clip_id, status, current_stage, started_at,
                    completed_at, error_message, config_snapshot
                ) VALUES (?, NULL, 'COMPLETED', 'COMPLETED', ?, ?, NULL, '{}')
                """,
                (job_id, datetime.now().isoformat(), datetime.now().isoformat()),
            )

        pending_id = "JOB-IN-PROGRESS"
        pending_output = jobs_dir / pending_id / "output.mp4"
        pending_output.parent.mkdir(parents=True)
        pending_output.write_bytes(b"still processing")
        os.utime(pending_output, (1, 1))
        db.conn.execute(
            """
            INSERT INTO jobs (
                job_id, source_clip_id, status, current_stage, started_at,
                completed_at, error_message, config_snapshot
            ) VALUES (?, NULL, 'RENDERING', 'RENDERING', ?, NULL, NULL, '{}')
            """,
            (pending_id, datetime.now().isoformat()),
        )
        db.conn.commit()

    warnings = runner._prune_old_generated_jobs()

    assert warnings == []
    assert len(runner.get_recent_outputs(limit=100)) == 11
    assert not (jobs_dir / "JOB-GENERATED-00").exists()
    assert not (jobs_dir / "JOB-GENERATED-01").exists()
    assert (jobs_dir / "JOB-GENERATED-02" / "story.json").exists()
    assert pending_output.exists()

    with Database(database_path) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 13


def _use_fake_clip_probe(monkeypatch):
    clip_manager_class = runner_module.ClipManager

    class FakeFFmpeg:
        def is_available(self):
            return True

        def probe(self, _path):
            return VideoInfo(
                duration=10.0,
                width=1920,
                height=1080,
                fps=30.0,
                codec="h264",
                bitrate=None,
                aspect_ratio="16:9",
            )

    monkeypatch.setattr(
        runner_module,
        "ClipManager",
        lambda db, clips_dir: clip_manager_class(
            db, clips_dir, ffmpeg=FakeFFmpeg()
        ),
    )


def test_ingest_directory_copies_files_and_resolves_name_collisions(
    monkeypatch, tmp_path
):
    _use_fake_clip_probe(monkeypatch)
    runner = LocalJobRunner(
        database_path=tmp_path / "data" / "media.db",
        clips_dir=tmp_path / "data" / "clips",
    )
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    first_source.mkdir()
    second_source.mkdir()
    first_file = first_source / "scene.mp4"
    second_file = second_source / "scene.mp4"
    first_file.write_bytes(b"first video content")
    second_file.write_bytes(b"different video content")

    assert runner.ingest_directory(first_source) == 1
    assert runner.ingest_directory(second_source) == 1

    first_staged = runner.clips_dir / "scene.mp4"
    second_staged = runner.clips_dir / (
        f"scene-{Database.hash_file(second_file)[:12]}.mp4"
    )
    assert first_staged.read_bytes() == first_file.read_bytes()
    assert second_staged.read_bytes() == second_file.read_bytes()
    with Database(runner.database_path) as db:
        clips = db.get_available_clips()
    assert {Path(clip.file_path) for clip in clips} == {
        first_staged.resolve(),
        second_staged.resolve(),
    }


def test_ingest_directory_is_idempotent_for_duplicate_content(
    monkeypatch, tmp_path
):
    _use_fake_clip_probe(monkeypatch)
    runner = LocalJobRunner(
        database_path=tmp_path / "data" / "media.db",
        clips_dir=tmp_path / "data" / "clips",
    )
    first_source = tmp_path / "first"
    duplicate_source = tmp_path / "duplicate"
    first_source.mkdir()
    duplicate_source.mkdir()
    (first_source / "scene.mp4").write_bytes(b"same video content")
    (duplicate_source / "renamed.mp4").write_bytes(b"same video content")

    assert runner.ingest_directory(first_source) == 1
    assert runner.ingest_directory(duplicate_source) == 0
    assert sorted(path.name for path in runner.clips_dir.iterdir()) == [
        "scene.mp4"
    ]
    with Database(runner.database_path) as db:
        assert len(db.get_available_clips()) == 1


def test_cloud_drive_import_downloads_into_managed_clip_library(
    monkeypatch, tmp_path
):
    _use_fake_clip_probe(monkeypatch)
    clips_dir = tmp_path / "data" / "clips"
    runner = LocalJobRunner(
        database_path=tmp_path / "data" / "media.db",
        clips_dir=clips_dir,
    )
    monkeypatch.setattr(
        runner_module,
        "load_settings",
        lambda: SimpleNamespace(
            google_client_id="publisher-client-id",
            google_client_secret="publisher-client-secret",
            google_token_file=tmp_path / "data" / "google_token.json",
        ),
    )

    class FakeDriveClient:
        def __init__(self, **kwargs):
            assert kwargs["folder_id"] == "drive-folder-id"

        def authenticate(self, raise_errors=False):
            assert raise_errors
            return True

        def verify_folder_access(self, raise_errors=False):
            assert raise_errors
            return True

        def list_files(self, supported_formats, raise_errors=False):
            assert "mp4" in supported_formats
            assert raise_errors
            return [{"id": "drive-file-id", "name": "cloud clip.mp4"}]

        def download_file(
            self, file_id, filename, output_dir, raise_errors=False
        ):
            assert file_id == "drive-file-id"
            assert filename.endswith(".mp4")
            assert raise_errors
            path = output_dir / filename
            path.write_bytes(b"downloaded video")
            return path

    monkeypatch.setattr("app.drive.client.DriveClient", FakeDriveClient)

    assert runner.ingest_drive_folder("drive-folder-id") == 1
    staged = clips_dir / "cloud clip.mp4"
    assert staged.read_bytes() == b"downloaded video"
    assert not list(clips_dir.parent.glob("drive-import-*"))
    with Database(runner.database_path) as db:
        clips = db.get_available_clips()
    assert len(clips) == 1
    assert Path(clips[0].file_path) == staged.resolve()
