"""Tests for explicit manual YouTube upload selection."""

from pathlib import Path

from dotenv import dotenv_values

from app.gui_main import upload_job_id
from app import pyside_gui


def test_upload_reuses_job_id_for_generated_video(tmp_path):
    video = tmp_path / "JOB-20261021-ABC123" / "output.mp4"

    assert upload_job_id(video) == "JOB-20261021-ABC123"


def test_upload_assigns_unique_record_id_for_arbitrary_video(tmp_path):
    video = tmp_path / "my-video.mp4"

    first = upload_job_id(video)
    second = upload_job_id(video)

    assert first.startswith("UPLOAD-")
    assert first != second


def test_web_research_upgrade_enables_live_search_once(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("WEB_RESEARCH_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setattr(pyside_gui, "ENV_FILE", env_file)
    monkeypatch.setattr(pyside_gui, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("WEB_RESEARCH_SETTINGS_VERSION", raising=False)

    pyside_gui._migrate_web_research_setting()

    values = dotenv_values(env_file)
    assert values["WEB_RESEARCH_ENABLED"] == "true"
    assert values["WEB_RESEARCH_SETTINGS_VERSION"] == "1"

    pyside_gui.set_key(str(env_file), "WEB_RESEARCH_ENABLED", "false")
    monkeypatch.setenv("WEB_RESEARCH_SETTINGS_VERSION", "1")
    pyside_gui._migrate_web_research_setting()

    assert dotenv_values(env_file)["WEB_RESEARCH_ENABLED"] == "false"
