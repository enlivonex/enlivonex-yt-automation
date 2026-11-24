"""Dedicated PySide workflow for reviewing and rendering local video moments."""

from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
import os
from threading import Event

from dotenv import set_key
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .editing import ClipEditPlan, ClipOutputSettings, ClipRange, RenderedClip
from .models import ClipAnalysis
from .moments import OllamaMomentRanker
from .renderer import LocalClipRenderer
from .service import LocalClipAnalyzer
from .source import LocalVideoCatalog
from .transcription import FasterWhisperTranscriber

LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"
WHISPER_MODEL_ENV = "FASTER_WHISPER_MODEL_PATH"


@dataclass(frozen=True)
class _RepurposeProgress:
    message: Callable[[str], None]
    fraction: Callable[[float], None]


class _RepurposeWorker(QThread):
    progress = Signal(str)
    progress_updated = Signal(int)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        operation: Callable[[_RepurposeProgress, Event], object],
        cancel_event: Event,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = cancel_event

    def run(self) -> None:
        def report_fraction(fraction: float) -> None:
            if not self.cancel_event.is_set():
                self.progress_updated.emit(round(100 * fraction))

        try:
            result = self.operation(
                _RepurposeProgress(self.progress.emit, report_fraction),
                self.cancel_event,
            )
        except CancelledError as error:
            self.cancelled.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


class LocalClipperPage(QWidget):
    """Local-only analysis, moment review, and draft rendering page."""

    prepare_upload_requested = Signal(str)

    def __init__(
        self,
        env_file: Path = Path(".env"),
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.env_file = env_file
        self.analysis: ClipAnalysis | None = None
        self._worker: _RepurposeWorker | None = None
        self._cancel_event: Event | None = None
        self._busy = False
        self._cancel_requested = False
        self._operation_successful = False
        self._build_ui()

    @property
    def is_busy(self) -> bool:
        return self._busy

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(14)

        title = QLabel("Make clips from your video")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Choose a video, review suggested moments, and save a short-form "
            "draft. Everything stays on this PC."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        source_card, source_layout = self._card("1. Choose a video")
        source_row = QHBoxLayout()
        self.source_path = QLineEdit()
        self.source_path.setReadOnly(True)
        self.source_path.setPlaceholderText("Choose a local video file")
        source_row.addWidget(self.source_path, 1)
        self.choose_source_button = QPushButton("Choose video")
        self.choose_source_button.clicked.connect(self._choose_source)
        source_row.addWidget(self.choose_source_button)
        source_layout.addLayout(source_row)

        model_row = QHBoxLayout()
        self.model_path = QLineEdit(os.environ.get(WHISPER_MODEL_ENV, ""))
        self.model_path.setPlaceholderText("Path to an installed Faster-Whisper model folder")
        self.model_path.editingFinished.connect(self._save_model_path)
        model_row.addWidget(self.model_path, 1)
        self.choose_model_button = QPushButton("Choose model folder")
        self.choose_model_button.clicked.connect(self._choose_model_folder)
        model_row.addWidget(self.choose_model_button)
        source_layout.addLayout(model_row)

        self.ollama_model = QLineEdit()
        self.ollama_model.setPlaceholderText("Installed local Ollama model")
        from app.config.settings import load_settings

        self.ollama_model.setText(load_settings().ollama_model)
        self.transcription_language = QComboBox()
        for label, language in (
            ("Auto-detect", None),
            ("English", "en"),
            ("Hindi", "hi"),
            ("Spanish", "es"),
            ("French", "fr"),
            ("German", "de"),
            ("Portuguese", "pt"),
            ("Arabic", "ar"),
            ("Chinese", "zh"),
            ("Japanese", "ja"),
            ("Russian", "ru"),
        ):
            self.transcription_language.addItem(label, language)
        settings_row = QFormLayout()
        settings_row.addRow("Ollama model (local server)", self.ollama_model)
        settings_row.addRow("Speech language", self.transcription_language)
        source_layout.addLayout(settings_row)
        outer.addWidget(source_card)

        analysis_card, analysis_layout = self._card("2. Find and select moments")
        analysis_options = QFormLayout()
        self.min_duration = self._duration_spin(30)
        self.max_duration = self._duration_spin(60)
        self.max_candidates = QSpinBox()
        self.max_candidates.setRange(1, 16)
        self.max_candidates.setValue(5)
        analysis_options.addRow("Minimum moment length (seconds)", self.min_duration)
        analysis_options.addRow("Maximum moment length (seconds)", self.max_duration)
        analysis_options.addRow("Suggestions to review", self.max_candidates)
        analysis_layout.addLayout(analysis_options)

        self.rights_confirmation = QCheckBox(
            "I have permission to edit and publish this video and its included material."
        )
        analysis_layout.addWidget(self.rights_confirmation)

        self.analyze_button = QPushButton("Analyze locally")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.clicked.connect(self._start_analysis)
        analysis_layout.addWidget(self.analyze_button)

        self.candidate_table = QTableWidget(0, 5)
        self.candidate_table.setHorizontalHeaderLabels(
            ("Use", "Start (s)", "End (s)", "Score", "Transcript and rationale")
        )
        self.candidate_table.setAlternatingRowColors(True)
        self.candidate_table.setWordWrap(True)
        self.candidate_table.setMinimumHeight(230)
        header = self.candidate_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.candidate_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        analysis_layout.addWidget(self.candidate_table)
        outer.addWidget(analysis_card, 1)

        render_card, render_layout = self._card("3. Style and save")
        self.resolution = QComboBox()
        self.resolution.addItem("Portrait - 1080 x 1920", (1080, 1920))
        self.resolution.addItem("Portrait - 720 x 1280", (720, 1280))
        self.resolution.addItem("Landscape - 1920 x 1080", (1920, 1080))
        self.resolution.addItem("Square - 1080 x 1080", (1080, 1080))
        self.fit_mode = QComboBox()
        self.fit_mode.addItem("Fill frame (crop)", "crop")
        self.fit_mode.addItem("Fit whole frame (pad)", "pad")
        self.fit_mode.addItem("Portrait with blurred background", "blurred")
        self.fit_mode.setCurrentIndex(2)
        self.caption_style = QComboBox()
        self.caption_style.addItem("Standard", "standard")
        self.caption_style.addItem("Bold yellow gaming", "gaming")
        self.caption_style.setCurrentIndex(1)
        self.captions_enabled = QCheckBox("Burn transcript captions")
        self.captions_enabled.setChecked(True)
        self.creator_credit = QLineEdit()
        self.creator_credit.setMaxLength(100)
        self.creator_credit.setPlaceholderText("YouTuber or source creator name")
        basic_fields = QFormLayout()
        basic_fields.addRow("Output format", self.resolution)
        basic_fields.addRow("", self.captions_enabled)
        basic_fields.addRow("Creator credit (optional)", self.creator_credit)
        render_layout.addLayout(basic_fields)

        self.advanced_style_button = QPushButton("Show optional styling controls")
        self.advanced_style_button.setCheckable(True)
        self.advanced_style_button.toggled.connect(
            self._set_advanced_style_visible
        )
        render_layout.addWidget(self.advanced_style_button)

        self.advanced_style_panel = QFrame()
        self.advanced_style_panel.setObjectName("card")
        advanced_fields = QFormLayout(self.advanced_style_panel)
        advanced_fields.addRow("Frame fit", self.fit_mode)
        advanced_fields.addRow("Caption style", self.caption_style)
        self.sharpness = self._adjustment_spin(0, 1.5, 0)
        self.brightness = self._adjustment_spin(-1, 1, 0)
        self.contrast = self._adjustment_spin(0.5, 1.5, 1)
        self.saturation = self._adjustment_spin(0, 2, 1)
        self.grain = QSpinBox()
        self.grain.setRange(0, 20)
        advanced_fields.addRow("Sharpness (0-1.5)", self.sharpness)
        advanced_fields.addRow("Brightness (-1-1)", self.brightness)
        advanced_fields.addRow("Contrast (0.5-1.5)", self.contrast)
        advanced_fields.addRow("Saturation (0-2)", self.saturation)
        advanced_fields.addRow("Grain (0-20)", self.grain)
        self.advanced_style_panel.hide()
        render_layout.addWidget(self.advanced_style_panel)

        output_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setPlaceholderText("Choose where to save the MP4 draft")
        output_row.addWidget(self.output_path, 1)
        self.choose_output_button = QPushButton("Choose output")
        self.choose_output_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.choose_output_button)
        render_layout.addLayout(output_row)

        action_row = QHBoxLayout()
        self.render_button = QPushButton("Render selected moments")
        self.render_button.setObjectName("primary")
        self.render_button.clicked.connect(self._start_render)
        self.open_output_button = QPushButton("Open rendered draft")
        self.open_output_button.setEnabled(False)
        self.open_output_button.clicked.connect(self._open_rendered_output)
        self.prepare_upload_button = QPushButton("Prepare for YouTube review")
        self.prepare_upload_button.setEnabled(False)
        self.prepare_upload_button.clicked.connect(self._prepare_upload)
        self.cancel_button = QPushButton("Cancel task")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._request_cancel)
        action_row.addWidget(self.render_button)
        action_row.addWidget(self.open_output_button)
        action_row.addWidget(self.prepare_upload_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        render_layout.addLayout(action_row)
        outer.addWidget(render_card)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        outer.addWidget(self.progress)
        self.status = QLabel("Choose a source video to begin.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self._update_actions()

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        return card, layout

    def _set_advanced_style_visible(self, visible: bool) -> None:
        self.advanced_style_panel.setVisible(visible)
        self.advanced_style_button.setText(
            "Hide optional styling controls"
            if visible
            else "Show optional styling controls"
        )

    @staticmethod
    def _duration_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(1, 600)
        spin.setDecimals(1)
        spin.setSingleStep(1)
        spin.setValue(value)
        return spin

    @staticmethod
    def _adjustment_spin(
        minimum: float, maximum: float, value: float
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.05)
        spin.setValue(value)
        return spin

    def _choose_source(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a local source video",
            "",
            "Video files (*.mp4 *.mov *.avi *.mkv *.webm *.flv)",
        )
        if not selected:
            return
        self.source_path.setText(selected)
        self.analysis = None
        self.candidate_table.setRowCount(0)
        self.output_path.clear()
        self.open_output_button.setProperty("path", "")
        self.prepare_upload_button.setProperty("path", "")
        self.open_output_button.setEnabled(False)
        self.prepare_upload_button.setEnabled(False)
        self.status.setText("Source selected. Analyze it to review suggestions.")
        self._update_actions()

    def _choose_model_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose an installed Faster-Whisper model folder"
        )
        if selected:
            self.model_path.setText(selected)
            self._save_model_path()

    def _save_model_path(self) -> None:
        model_path = self.model_path.text().strip()
        if not model_path:
            return
        try:
            set_key(str(self.env_file), WHISPER_MODEL_ENV, model_path)
        except OSError as error:
            self.status.setText(f"Could not save model path: {error}")

    def _choose_output(self) -> None:
        source = self.source_path.text().strip()
        suggested = (
            str(Path(source).with_name(f"{Path(source).stem}-clips.mp4"))
            if source
            else ""
        )
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save rendered draft", suggested, "MP4 video (*.mp4)"
        )
        if selected:
            if Path(selected).suffix.lower() != ".mp4":
                selected += ".mp4"
            self.output_path.setText(selected)
            self._update_actions()

    def _start_analysis(self) -> None:
        if not self._confirm_rights():
            return
        source_text = self.source_path.text().strip()
        model_text = self.model_path.text().strip()
        if not source_text:
            self.status.setText("Choose an existing local video file.")
            return
        if not model_text:
            self.status.setText(
                "Choose the folder for an already-installed Faster-Whisper model."
            )
            return
        video_path = Path(source_text).expanduser()
        model_path = Path(model_text).expanduser()
        ollama_model = self.ollama_model.text().strip()
        if not video_path.is_file():
            self.status.setText("Choose an existing local video file.")
            return
        if not model_path.is_dir():
            self.status.setText(
                "Choose the folder for an already-installed Faster-Whisper model."
            )
            return
        if not ollama_model:
            self.status.setText("Enter the name of an installed local Ollama model.")
            return
        if self.max_duration.value() < self.min_duration.value():
            self.status.setText(
                "Maximum moment length must be at least the minimum length."
            )
            return
        self._save_model_path()
        self.analysis = None
        self.candidate_table.setRowCount(0)
        options = (
            video_path,
            model_path,
            ollama_model,
            self.min_duration.value(),
            self.max_duration.value(),
            self.max_candidates.value(),
            self.transcription_language.currentData(),
        )
        self._start_worker(
            "analysis",
            lambda progress, cancel_event: self._analyze(
                options, progress.message, cancel_event
            ),
            "Starting local analysis...",
        )

    @staticmethod
    def _analyze(
        options, report: Callable[[str], None], cancel_event: Event
    ) -> ClipAnalysis:
        from app.ai.ollama import OllamaClient
        from app.config.settings import load_settings

        (
            video_path,
            model_path,
            ollama_model,
            min_duration,
            max_duration,
            max_candidates,
            language,
        ) = options
        settings = load_settings()
        analyzer = LocalClipAnalyzer(
            catalog=LocalVideoCatalog(),
            transcriber=FasterWhisperTranscriber(model_path),
            moment_ranker=OllamaMomentRanker(
                OllamaClient(
                    base_url=LOCAL_OLLAMA_URL,
                    model=ollama_model,
                    timeout_seconds=settings.ollama_timeout_seconds,
                )
            ),
        )
        return analyzer.analyze(
            video_path,
            min_duration=min_duration,
            max_duration=max_duration,
            max_candidates=max_candidates,
            language=language,
            progress_callback=report,
            cancel_event=cancel_event,
        )

    def _populate_candidates(self, analysis: ClipAnalysis) -> None:
        self.analysis = analysis
        self.candidate_table.setRowCount(len(analysis.candidates))
        for row, candidate in enumerate(analysis.candidates):
            include = QTableWidgetItem()
            include.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            include.setCheckState(Qt.CheckState.Unchecked)
            self.candidate_table.setItem(row, 0, include)

            for column, value in ((1, candidate.start_seconds), (2, candidate.end_seconds)):
                spin = QDoubleSpinBox()
                spin.setRange(0, analysis.source.duration_seconds)
                spin.setDecimals(2)
                spin.setSingleStep(0.25)
                spin.setValue(value)
                self.candidate_table.setCellWidget(row, column, spin)

            score = QTableWidgetItem(f"{candidate.score:.2f}")
            score.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.candidate_table.setItem(row, 3, score)
            summary = QLabel(
                f"{candidate.transcript}\n\nWhy: {candidate.rationale}"
            )
            summary.setWordWrap(True)
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.candidate_table.setCellWidget(row, 4, summary)
        self.status.setText(
            f"Analysis complete: {len(analysis.transcript)} transcript segment(s), "
            f"{len(analysis.candidates)} suggestion(s). Review and select moments."
        )
        self._update_actions()

    def _create_edit_plan(self) -> ClipEditPlan:
        if self.analysis is None:
            raise ValueError("Analyze a source video before rendering.")
        if not self.rights_confirmation.isChecked():
            raise ValueError("Confirm that you have permission to edit this material.")
        ranges = []
        for row in range(self.candidate_table.rowCount()):
            include = self.candidate_table.item(row, 0)
            if include is None or include.checkState() != Qt.CheckState.Checked:
                continue
            start_widget = self.candidate_table.cellWidget(row, 1)
            end_widget = self.candidate_table.cellWidget(row, 2)
            if not isinstance(start_widget, QDoubleSpinBox) or not isinstance(
                end_widget, QDoubleSpinBox
            ):
                raise RuntimeError("A selected moment has invalid time controls.")
            ranges.append(ClipRange(start_widget.value(), end_widget.value()))
        if not ranges:
            raise ValueError("Select at least one suggested moment to render.")
        width, height = self.resolution.currentData()
        settings = ClipOutputSettings(
            width=width,
            height=height,
            fit_mode=self.fit_mode.currentData(),
            caption_style=self.caption_style.currentData(),
            burn_captions=self.captions_enabled.isChecked(),
            sharpness=self.sharpness.value(),
            brightness=self.brightness.value(),
            contrast=self.contrast.value(),
            saturation=self.saturation.value(),
            grain=self.grain.value(),
            creator_credit=self.creator_credit.text().strip(),
        )
        return ClipEditPlan(
            source=self.analysis.source,
            ranges=tuple(ranges),
            transcript=self.analysis.transcript,
            settings=settings,
        )

    def _start_render(self) -> None:
        if not self._confirm_rights():
            return
        try:
            plan = self._create_edit_plan()
        except (RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            return
        destination = Path(self.output_path.text().strip()).expanduser()
        if not self.output_path.text().strip():
            self.status.setText("Choose an MP4 output path before rendering.")
            return
        if destination.suffix.lower() != ".mp4":
            self.status.setText("The output filename must end in .mp4.")
            return
        if destination.resolve() == plan.source.path:
            self.status.setText("The output must not overwrite the source video.")
            return
        if destination.exists():
            self.status.setText(
                "That output file already exists. Choose a new filename; drafts "
                "are never overwritten."
            )
            return
        self._start_worker(
            "render",
            lambda progress, cancel_event: self._render(
                plan,
                destination,
                progress.message,
                progress.fraction,
                cancel_event,
            ),
            "Rendering selected moments locally...",
            determinate=True,
        )

    @staticmethod
    def _render(
        plan: ClipEditPlan,
        destination: Path,
        report: Callable[[str], None],
        progress: Callable[[float], None],
        cancel_event: Event,
    ) -> RenderedClip:
        return LocalClipRenderer().render(
            plan,
            destination,
            cancel_event=cancel_event,
            progress_callback=progress,
            stage_callback=report,
        )

    def _start_worker(
        self,
        operation_name: str,
        operation: Callable[[_RepurposeProgress, Event], object],
        status: str,
        *,
        determinate: bool = False,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel_requested = False
        self._operation_successful = False
        self._cancel_event = Event()
        self._operation_name = operation_name
        self.progress.setRange(0, 100 if determinate else 0)
        self.progress.setValue(0)
        self.status.setText(status)
        self._update_actions()
        worker = _RepurposeWorker(operation, self._cancel_event, self)
        worker.progress.connect(self._show_progress_message)
        worker.progress_updated.connect(self._show_progress_value)
        worker.succeeded.connect(self._operation_succeeded)
        worker.failed.connect(self._operation_failed)
        worker.cancelled.connect(self._operation_cancelled)
        worker.finished.connect(self._operation_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _operation_succeeded(self, result: object) -> None:
        if self._operation_name == "analysis":
            if not isinstance(result, ClipAnalysis):
                self._operation_failed("Analysis returned an unexpected result.")
                return
            self._populate_candidates(result)
        elif self._operation_name == "render":
            if not isinstance(result, RenderedClip):
                self._operation_failed("Rendering returned an unexpected result.")
                return
            self.open_output_button.setProperty("path", str(result.path))
            self.prepare_upload_button.setProperty("path", str(result.path))
            self.open_output_button.setEnabled(result.path.is_file())
            self.prepare_upload_button.setEnabled(result.path.is_file())
            self.status.setText(
                f"Draft rendered: {result.path} "
                f"({result.duration_seconds:.1f}s, {result.width}x{result.height})."
            )
        self._operation_successful = True
        self._update_actions()

    def _operation_failed(self, message: str) -> None:
        self._operation_successful = False
        self.status.setText(f"{self._operation_name.capitalize()} failed: {message}")

    def _operation_cancelled(self, message: str) -> None:
        self._operation_successful = False
        self.status.setText(
            f"{self._operation_name.capitalize()} cancelled: {message} "
            "Temporary render files were cleaned up."
        )

    def _show_progress_message(self, message: str) -> None:
        if not self._cancel_requested:
            self.status.setText(message)

    def _show_progress_value(self, value: int) -> None:
        if not self._cancel_requested:
            self.progress.setValue(value)

    def _request_cancel(self) -> None:
        if not self._busy or self._cancel_event is None:
            return
        self._cancel_requested = True
        self._cancel_event.set()
        self.status.setText(
            "Cancellation requested. The current local model step will finish, "
            "then temporary render files will be removed."
        )
        self._update_actions()

    def _operation_finished(self) -> None:
        self._busy = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self._operation_successful else 0)
        self._cancel_event = None
        self._cancel_requested = False
        self._worker = None
        self._update_actions()

    def _confirm_rights(self) -> bool:
        if self.rights_confirmation.isChecked():
            return True
        self.status.setText(
            "Confirm that you have permission to edit and publish this video and its included material."
        )
        return False

    def _open_rendered_output(self) -> None:
        output = self.open_output_button.property("path")
        if not output or not Path(output).is_file():
            self.status.setText("The rendered draft could not be found.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output))):
            QMessageBox.warning(
                self,
                "Could not open draft",
                f"Open the rendered draft manually:\n{output}",
            )

    def _prepare_upload(self) -> None:
        if not self._confirm_rights():
            return
        output = self.prepare_upload_button.property("path")
        if not output or not Path(output).is_file():
            self.status.setText("The rendered draft could not be found.")
            return
        self.prepare_upload_requested.emit(str(output))

    def _update_actions(self) -> None:
        has_source = bool(self.source_path.text().strip())
        has_candidates = self.analysis is not None and bool(
            self.analysis.candidates
        )
        self.choose_source_button.setEnabled(not self._busy)
        self.choose_model_button.setEnabled(not self._busy)
        self.analyze_button.setEnabled(not self._busy and has_source)
        self.render_button.setEnabled(not self._busy and has_candidates)
        self.choose_output_button.setEnabled(not self._busy)
        self.cancel_button.setEnabled(
            self._busy and not self._cancel_requested
        )
