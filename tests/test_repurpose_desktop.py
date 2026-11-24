"""Tests for the local repurposing desktop workflow."""

import hashlib
import os
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

from app.repurpose import desktop
from app.repurpose.models import (
    ClipAnalysis,
    MomentCandidate,
    TranscriptSegment,
    VideoSource,
)


@pytest.fixture(scope="module")
def qapplication():
    application = QApplication.instance() or QApplication([])
    yield application


def create_analysis(tmp_path: Path) -> ClipAnalysis:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source video")
    source = VideoSource(
        path=video.resolve(),
        sha256=hashlib.sha256(b"source video").hexdigest(),
        file_size_bytes=12,
        duration_seconds=60,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        aspect_ratio="16:9",
    )
    return ClipAnalysis(
        source=source,
        transcript=(TranscriptSegment(1, 8, "An example transcript."),),
        candidates=(
            MomentCandidate(1, 8, "An example transcript.", 0.9, "Clear thought."),
        ),
    )


def test_review_controls_create_selected_edit_plan(tmp_path, qapplication):
    analysis = create_analysis(tmp_path)
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    assert page.captions_enabled.isChecked()
    page.rights_confirmation.setChecked(True)
    page._populate_candidates(analysis)
    page.candidate_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    start = page.candidate_table.cellWidget(0, 1)
    end = page.candidate_table.cellWidget(0, 2)
    assert isinstance(start, QDoubleSpinBox)
    assert isinstance(end, QDoubleSpinBox)
    start.setValue(2.5)
    end.setValue(9.25)
    page.resolution.setCurrentIndex(1)
    page.sharpness.setValue(0.5)
    page.brightness.setValue(0.2)
    page.contrast.setValue(1.1)
    page.saturation.setValue(1.2)
    page.grain.setValue(4)
    page.creator_credit.setText("Example Creator")

    plan = page._create_edit_plan()

    assert [(clip.start_seconds, clip.end_seconds) for clip in plan.ranges] == [
        (2.5, 9.25)
    ]
    assert (plan.settings.width, plan.settings.height) == (720, 1280)
    assert plan.settings.fit_mode == "blurred"
    assert plan.settings.caption_style == "gaming"
    assert plan.settings.burn_captions
    assert plan.settings.sharpness == 0.5
    assert plan.settings.brightness == 0.2
    assert plan.settings.contrast == 1.1
    assert plan.settings.saturation == 1.2
    assert plan.settings.grain == 4
    assert plan.settings.creator_credit == "Example Creator"
    page.close()


def test_optional_style_controls_are_hidden_until_requested(
    tmp_path, qapplication
):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")

    assert page.min_duration.value() == 30
    assert page.max_candidates.value() == 5
    assert page.advanced_style_panel.isHidden()

    page.advanced_style_button.click()
    assert not page.advanced_style_panel.isHidden()
    page.advanced_style_button.click()
    assert page.advanced_style_panel.isHidden()
    page.close()


def test_render_plan_requires_rights_confirmation_and_selected_moment(
    tmp_path, qapplication
):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page._populate_candidates(create_analysis(tmp_path))

    with pytest.raises(ValueError, match="permission"):
        page._create_edit_plan()

    page.rights_confirmation.setChecked(True)
    with pytest.raises(ValueError, match="Select at least one"):
        page._create_edit_plan()
    page.close()


def test_desktop_analysis_uses_loopback_ollama_and_reports_stages(
    tmp_path, monkeypatch
):
    analysis = create_analysis(tmp_path)
    model_path = tmp_path / "whisper-model"
    model_path.mkdir()
    client_options = []
    callbacks = []

    class FakeOllamaClient:
        def __init__(self, **kwargs):
            client_options.append(kwargs)

    class FakeAnalyzer:
        def analyze(self, _video_path, **kwargs):
            kwargs["progress_callback"]("Transcribing locally...")
            return analysis

    monkeypatch.setattr(desktop, "LocalClipAnalyzer", lambda **_kwargs: FakeAnalyzer())
    monkeypatch.setattr(desktop, "LocalVideoCatalog", lambda: object())
    monkeypatch.setattr(
        desktop, "FasterWhisperTranscriber", lambda _model_path: object()
    )
    monkeypatch.setattr(desktop, "OllamaMomentRanker", lambda client: client)
    monkeypatch.setattr("app.ai.ollama.OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(
        "app.config.settings.load_settings",
        lambda: SimpleNamespace(ollama_timeout_seconds=45),
    )

    result = desktop.LocalClipperPage._analyze(
        (
            analysis.source.path,
            model_path,
            "qwen2.5:3b",
            15,
            60,
            8,
            "en",
        ),
        callbacks.append,
        Event(),
    )

    assert result is analysis
    assert callbacks == ["Transcribing locally..."]
    assert client_options == [
        {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen2.5:3b",
            "timeout_seconds": 45,
        }
    ]
    analyze_options = []
    monkeypatch.setattr(
        desktop,
        "LocalClipAnalyzer",
        lambda **_kwargs: SimpleNamespace(
            analyze=lambda _path, **kwargs: analyze_options.append(kwargs)
            or analysis
        ),
    )
    desktop.LocalClipperPage._analyze(
        (
            analysis.source.path,
            model_path,
            "qwen2.5:3b",
            15,
            60,
            8,
            "en",
        ),
        callbacks.append,
        Event(),
    )
    assert analyze_options[0]["language"] == "en"


def test_cancel_button_stops_worker_and_restores_page(tmp_path, qapplication):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")

    def wait_for_cancel(_progress, cancel_event):
        cancel_event.wait(timeout=2)
        if cancel_event.is_set():
            raise CancelledError("Cancellation requested by the user.")
        return None

    page._start_worker(
        "analysis", wait_for_cancel, "Starting cancellable analysis..."
    )
    assert page.cancel_button.isEnabled()

    page.cancel_button.click()
    assert page._cancel_requested
    assert page._worker is not None
    assert page._worker.wait(3000)
    qapplication.processEvents()

    assert not page.is_busy
    assert "cancelled" in page.status.text().lower()
    assert page.cancel_button.isEnabled() is False
    page.close()
