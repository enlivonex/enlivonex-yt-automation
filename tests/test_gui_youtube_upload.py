"""Tests for explicit manual YouTube upload selection."""

from pathlib import Path

from app.gui_main import upload_job_id


def test_upload_reuses_job_id_for_generated_video(tmp_path):
    video = tmp_path / "JOB-20261021-ABC123" / "output.mp4"

    assert upload_job_id(video) == "JOB-20261021-ABC123"


def test_upload_assigns_unique_record_id_for_arbitrary_video(tmp_path):
    video = tmp_path / "my-video.mp4"

    first = upload_job_id(video)
    second = upload_job_id(video)

    assert first.startswith("UPLOAD-")
    assert first != second
