"""Regression tests for GUI generation progress feedback."""

from app.gui_main import ACTIVITY_MESSAGES, JOB_PROGRESS


def test_progress_tracks_job_stages_and_reaches_completion():
    stage_progress = [
        JOB_PROGRESS[stage]
        for stage in (
            "QUEUED",
            "SELECTING_CLIP",
            "GENERATING_STORY",
            "GENERATING_VOICE",
            "GENERATING_CAPTIONS",
            "RENDERING",
            "GENERATING_METADATA",
            "COMPLETED",
        )
    ]

    assert stage_progress == sorted(stage_progress)
    assert stage_progress[-1] == 100


def test_activity_messages_are_varied():
    assert len(ACTIVITY_MESSAGES) >= 5
