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
from app.repurpose.editing import RenderedClip
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
    assert plan.settings.hardware_acceleration == "auto"
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
    assert page.max_candidates.value() == 4
    assert page.max_candidates.maximum() == 4
    assert page.processing_device.currentData() == "auto"
    assert not hasattr(page, "moment_finding_device")
    assert page.manual_start.value() == 0
    assert page.manual_end.value() == 30
    assert page.workflow_mode.currentData() == "suggest"
    assert not page.analysis_card.isHidden()
    assert page.manual_range_panel.isHidden()
    assert not page.manual_clip_button.isEnabled()
    assert page.advanced_style_panel.isHidden()

    page.advanced_style_button.click()
    assert not page.advanced_style_panel.isHidden()
    page.advanced_style_button.click()
    assert page.advanced_style_panel.isHidden()
    page.close()


def test_gpu_only_mode_requires_a_detected_nvidia_gpu(tmp_path, qapplication):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")

    assert page.processing_device.findData("cuda") == -1
    page.update_gpu_availability(True, "NVIDIA Test GPU")
    render_gpu_index = page.processing_device.findData("cuda")
    assert render_gpu_index >= 0
    assert "NVIDIA Test GPU detected" in page.acceleration_help.text()
    assert "Moment finding stays CPU-only" in page.acceleration_help.text()

    page.processing_device.setCurrentIndex(render_gpu_index)
    page.update_gpu_availability(False)

    assert page.processing_device.findData("cuda") == -1
    assert page.processing_device.currentData() == "auto"
    assert "NVIDIA-only render mode is unavailable" in page.acceleration_help.text()
    page.close()


def test_saved_gpu_mode_is_restored_after_a_detected_gpu_scan(
    tmp_path, qapplication
):
    env_file = tmp_path / "settings.env"
    page = desktop.LocalClipperPage(env_file=env_file)
    page.update_gpu_availability(True, "NVIDIA Test GPU")
    page.processing_device.setCurrentIndex(
        page.processing_device.findData("cuda")
    )
    page.close()

    reopened = desktop.LocalClipperPage(env_file=env_file)
    assert reopened.processing_device.currentData() == "auto"
    reopened.update_gpu_availability(True, "NVIDIA Test GPU")

    assert reopened.processing_device.currentData() == "cuda"
    reopened.close()


def test_saved_preferences_no_longer_include_moment_finding_device(
    tmp_path, qapplication
):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page._save_clip_preferences()

    saved = desktop.dotenv_values(str(page.env_file))["LOCAL_CLIP_PREFERENCES"]
    assert "moment_finding_device" not in saved
    page.close()


def test_clipper_mode_and_preferences_are_saved_between_pages(
    tmp_path, qapplication
):
    env_file = tmp_path / "settings.env"
    page = desktop.LocalClipperPage(env_file=env_file)

    page.workflow_mode.setCurrentIndex(page.workflow_mode.findData("exact"))
    page.max_candidates.setValue(3)
    page.contrast.setValue(1.2)
    page.processing_device.setCurrentIndex(
        page.processing_device.findData("cpu")
    )

    assert page.analysis_card.isHidden()
    assert not page.manual_range_panel.isHidden()
    page.close()

    reopened = desktop.LocalClipperPage(env_file=env_file)
    assert reopened.workflow_mode.currentData() == "exact"
    assert reopened.max_candidates.value() == 3
    assert reopened.contrast.value() == 1.2
    assert reopened.processing_device.currentData() == "cpu"
    assert reopened.analysis_card.isHidden()
    assert not reopened.manual_range_panel.isHidden()
    reopened.close()


def test_render_exports_selected_moments_as_separate_mp4s(
    tmp_path, monkeypatch
):
    source_analysis = create_analysis(tmp_path)
    plan = desktop.ClipEditPlan(
        source=source_analysis.source,
        ranges=(desktop.ClipRange(1, 8), desktop.ClipRange(20, 27)),
        transcript=source_analysis.transcript,
    )
    base_path = tmp_path / "renders" / "highlights.mp4"
    captured = []

    class FakeRenderer:
        def render(self, clip_plan, output_path, **options):
            assert len(clip_plan.ranges) == 1
            captured.append((clip_plan.ranges[0], output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"clip")
            options["progress_callback"](0.5)
            options["stage_callback"]("rendering")
            return RenderedClip(output_path, 7, 1080, 1920, True, 1)

    monkeypatch.setattr(desktop, "LocalClipRenderer", FakeRenderer)
    progress = []
    stages = []

    results = desktop.LocalClipperPage._render(
        plan,
        base_path,
        stages.append,
        progress.append,
        Event(),
    )

    assert [clip.path.name for clip in results] == [
        "highlights-clip-01.mp4",
        "highlights-clip-02.mp4",
    ]
    assert [clip_range for clip_range, _path in captured] == list(plan.ranges)
    assert progress == [0.25, 0.75]
    assert stages == ["Clip 1 of 2: rendering", "Clip 2 of 2: rendering"]


def test_clip_progress_shows_percentage_and_estimated_remaining_time(
    tmp_path, qapplication, monkeypatch
):
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page.progress.setRange(0, 100)
    page._operation_started_at = 100
    page._progress_stage = "Transcribing locally"
    monkeypatch.setattr(desktop.time, "monotonic", lambda: 130)

    page._show_progress_value(50)

    assert "50% complete" in page.status.text()
    assert "approximately 30 sec remaining" in page.status.text()
    page.close()


def test_manual_clip_uses_exact_range_without_running_analysis(
    tmp_path, qapplication, monkeypatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source video")
    model_path = tmp_path / "whisper-model"
    model_path.mkdir()
    output = tmp_path / "manual.mp4"
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page.workflow_mode.setCurrentIndex(page.workflow_mode.findData("exact"))
    page.source_path.setText(str(source))
    page.model_path.setText(str(model_path))
    page.output_path.setText(str(output))
    page.rights_confirmation.setChecked(True)
    page.manual_start.setValue(12.25)
    page.manual_end.setValue(18.5)
    page.transcription_language.setCurrentIndex(1)
    page._update_actions()
    captured = {}
    rendered = RenderedClip(output, 6.25, 1080, 1920, True, 1)

    class FakeManualClipper:
        def render(self, *args, **kwargs):
            captured["manual_args"] = args
            captured["manual_options"] = kwargs
            return rendered

    monkeypatch.setattr(
        desktop,
        "FasterWhisperTranscriber",
        lambda path, **_kwargs: ("transcriber", path),
    )
    monkeypatch.setattr(desktop, "LocalManualClipper", FakeManualClipper)

    def capture_worker(operation_name, operation, status, **options):
        captured["worker"] = (
            operation_name,
            operation,
            status,
            options,
        )

    monkeypatch.setattr(page, "_start_worker", capture_worker)
    page._start_manual_clip()

    operation_name, operation, status, options = captured["worker"]
    assert page.analysis is None
    assert page.manual_clip_button.isEnabled()
    assert operation_name == "manual clip"
    assert "exact-range" in status
    assert options == {"determinate": True}
    assert operation(
        desktop._RepurposeProgress(lambda _message: None, lambda _value: None),
        Event(),
    ) is rendered
    assert captured["manual_args"][:3] == (source, 12.25, 18.5)
    assert captured["manual_args"][4].burn_captions
    assert captured["manual_options"]["transcriber"] == (
        "transcriber",
        model_path,
    )
    assert captured["manual_options"]["language"] == "en"
    page.close()


def test_manual_clip_rejects_ranges_over_90_seconds(tmp_path, qapplication):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source video")
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page.source_path.setText(str(source))
    page.rights_confirmation.setChecked(True)
    page.manual_start.setValue(0)
    page.manual_end.setValue(90.01)

    page._start_manual_clip()

    assert "must not exceed 90 seconds" in page.status.text()
    assert not page.is_busy
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


def test_clip_plan_rejects_more_than_four_selected_moments(
    tmp_path, qapplication
):
    base_analysis = create_analysis(tmp_path)
    candidates = tuple(
        MomentCandidate(
            start_seconds=1 + index * 10,
            end_seconds=8 + index * 10,
            transcript=f"Moment {index + 1}.",
            score=0.9,
            rationale="A complete thought.",
        )
        for index in range(5)
    )
    analysis = ClipAnalysis(
        source=base_analysis.source,
        transcript=base_analysis.transcript,
        candidates=candidates,
    )
    page = desktop.LocalClipperPage(env_file=tmp_path / "settings.env")
    page.rights_confirmation.setChecked(True)
    page._populate_candidates(analysis)
    for row in range(page.candidate_table.rowCount()):
        page.candidate_table.item(row, 0).setCheckState(Qt.CheckState.Checked)

    with pytest.raises(ValueError, match="no more than 4 clips"):
        page._create_edit_plan()
    page.close()


def test_desktop_analysis_uses_loopback_ollama_and_reports_stages(
    tmp_path, monkeypatch
):
    analysis = create_analysis(tmp_path)
    model_path = tmp_path / "whisper-model"
    model_path.mkdir()
    client_options = []
    transcriber_options = []
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
        desktop,
        "FasterWhisperTranscriber",
        lambda _model_path, **kwargs: transcriber_options.append(kwargs)
        or object(),
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
    assert transcriber_options[0]["device"] == "cpu"
    assert client_options == [
        {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen2.5:3b",
            "timeout_seconds": 45,
            "cpu_only": True,
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
