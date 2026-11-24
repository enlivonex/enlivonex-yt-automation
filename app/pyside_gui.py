"""PySide6 desktop interface for local video generation and YouTube uploads."""

import json
import math
import queue
import random
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv, set_key
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import load_settings
from app.database.db import Database
from app.gui_helpers import ACTIVITY_MESSAGES, JOB_PROGRESS, upload_job_id
from app.metadata.generator import create_metadata_generator
from app.metadata.models import YouTubeMetadata
from app.orchestration.runner import LocalJobRunner
from app.repurpose.desktop import LocalClipperPage
from app.story.models import Story, StorySegment
from app.story.provider import GEMINI_REQUEST_TIMEOUT_SECONDS
from app.youtube.accounts import YouTubeAccountStore
from app.youtube.uploader import YouTubeUploader

ENV_FILE = Path(".env")
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
APP_ICON_PATH = ASSETS_DIR / "enlivonex.ico"
BRAND_WORDMARK_PATH = ASSETS_DIR / "enlivonex_wordmark.png"


class PySideDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Enlivonex YouTube Automation")
        if APP_ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1180, 860)
        self.setMinimumSize(800, 580)
        self._configure_palette(QApplication.instance())
        QApplication.instance().setStyleSheet(self._stylesheet())

        if not ENV_FILE.exists():
            ENV_FILE.touch()
        load_dotenv(ENV_FILE, override=True)
        # Create the schema before the dashboard reads upload and creation
        # counts. A clean install has no media.db yet, so limit checks would
        # otherwise fail while querying youtube_videos during window startup.
        with Database(Path("data/media.db")) as database:
            database.initialize()
        settings = load_settings()
        self.youtube_accounts = YouTubeAccountStore(
            Path("data/youtube_accounts"),
            legacy_token_path=settings.youtube_token_file,
        )
        self.youtube_accounts.migrate_legacy_token()
        self._account_labels = {}

        self.runner = LocalJobRunner()
        self.events = queue.Queue()
        self._last_scan = None
        self.active_task = False
        self.rotate_activity_messages = True
        self.active_job_id = None
        self.current_stage = None
        self.activity_message = ACTIVITY_MESSAGES[0]
        self.output_path = None
        self.recent_output_paths = []
        self.upload_path = None
        self.youtube_id = None

        self._build_shell()
        self._build_create_page()
        self._build_settings_page()
        self._build_youtube_page()
        self._build_repurpose_page()
        self._load_settings()
        self._refresh_recent_outputs()
        self._update_youtube_status()
        self._refresh_limits_status()

        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._process_events)
        self.event_timer.start(250)
        self.activity_timer = QTimer(self)
        self.activity_timer.timeout.connect(self._rotate_activity_message)

    @staticmethod
    def _stylesheet():
        return """
            QWidget { color: #17243a; font-family: "Segoe UI"; font-size: 10pt; }
            QMainWindow, QWidget#root, QStackedWidget, QWidget#contentPage {
                background-color: #f3f6fb;
            }
            QFrame#sidebar { background-color: #13233f; border-radius: 18px; }
            QLabel#brand { color: #ffffff; font-size: 19pt; font-weight: 700; }
            QLabel#brandCaption { color: #c6d4e9; font-size: 8pt; font-weight: 700; }
            QLabel#pageTitle { color: #13233f; font-size: 23pt; font-weight: 700; }
            QLabel#pageSubtitle { color: #425570; font-size: 10pt; }
            QLabel#cardTitle { color: #17243a; font-size: 12pt; font-weight: 700; }
            QLabel#muted { color: #425570; }
            QLabel#status { color: #17243a; background-color: #f7f9fd; border: 1px solid #cbd5e1;
                            border-radius: 9px; padding: 10px; }
            QFrame#card { background-color: #ffffff; color: #17243a;
                          border: 1px solid #d5deea; border-radius: 14px; }
            QPushButton { background-color: #e3eaf4; color: #17243a; border: 0; border-radius: 8px;
                          padding: 10px 14px; font-weight: 600; }
            QPushButton:hover { background-color: #d3deed; }
            QPushButton:disabled { color: #4b5e78; background-color: #dfe6f0; }
            QPushButton#primary { color: #ffffff; background-color: #2563eb; padding: 12px 16px; }
            QPushButton#primary:hover { background-color: #1d4ed8; }
            QPushButton#primary:disabled { color: #ffffff; background-color: #4b6cb3; }
            QPushButton#nav { color: #e1e9f5; background-color: transparent; text-align: left;
                              padding: 12px 14px; }
            QPushButton#nav:hover, QPushButton#nav:checked {
                color: #ffffff; background-color: #29446d;
            }
            QPushButton#nav:disabled { color: #c6d4e9; }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #ffffff; color: #17243a; border: 1px solid #cbd5e1;
                border-radius: 8px; min-height: 30px; padding: 5px 10px;
                selection-background-color: #dce8fc;
            }
            QPlainTextEdit, QListWidget {
                background-color: #ffffff; color: #17243a; border: 1px solid #cbd5e1;
                border-radius: 8px; padding: 8px;
                selection-background-color: #dce8fc;
            }
            QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
            QPlainTextEdit:disabled { color: #4b5e78; background-color: #edf1f6; }
            QComboBox QAbstractItemView { color: #17243a; background-color: #ffffff;
                                          selection-color: #17243a; selection-background-color: #dce8fc; }
            QComboBox::drop-down { border: 0; width: 25px; }
            QProgressBar { color: #17243a; background-color: #dfe6f0; border: 0;
                           border-radius: 5px; min-height: 10px; text-align: center; }
            QProgressBar::chunk { background-color: #2563eb; border-radius: 5px; }
            QCheckBox { color: #17243a; spacing: 8px; }
            QCheckBox:disabled { color: #4b5e78; }
            QScrollArea { border: 0; background-color: #f3f6fb; }
            QScrollBar:vertical { background-color: #edf1f6; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background-color: #aebbd0; border-radius: 4px; min-height: 25px; }
        """

    @staticmethod
    def _configure_palette(application):
        application.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.AlternateBase, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Highlight, Qt.GlobalColor.blue)
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.PlaceholderText, Qt.GlobalColor.darkGray)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, Qt.GlobalColor.darkGray)
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            Qt.GlobalColor.darkGray,
        )
        application.setPalette(palette)

    def _build_shell(self):
        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(220)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(16, 22, 16, 18)
        nav.setSpacing(9)
        brand = QLabel("Enlivonex")
        brand.setObjectName("brand")
        if BRAND_WORDMARK_PATH.is_file():
            wordmark = QPixmap(str(BRAND_WORDMARK_PATH))
            if not wordmark.isNull():
                brand.setPixmap(
                    wordmark.scaled(
                        148,
                        50,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                brand.setAccessibleName("Enlivonex")
        nav.addWidget(brand)
        caption = QLabel("LOCAL-FIRST SHORTS STUDIO")
        caption.setObjectName("brandCaption")
        nav.addWidget(caption)
        nav.addSpacing(28)

        self.nav_buttons = []
        for label, index in (
            ("01   Create video", 0),
            ("02   AI settings", 1),
            ("03   YouTube studio", 2),
            ("04   Local AI clipper", 3),
        ):
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=index: self._show_page(page))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        nav.addStretch(1)
        footer = QLabel("CREATE  |  REVIEW  |  PUBLISH")
        footer.setObjectName("brandCaption")
        nav.addWidget(footer)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self._show_page(0)

    def _page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("contentPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        outer.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)
        outer.addWidget(description)
        return page, outer

    @staticmethod
    def _card(title, subtitle=None):
        card = QFrame()
        card.setObjectName("card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
        return card, layout

    @staticmethod
    def _scrollable(content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        viewport_palette = scroll.viewport().palette()
        viewport_palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.white)
        scroll.viewport().setPalette(viewport_palette)
        scroll.viewport().setAutoFillBackground(True)
        scroll.setWidget(content)
        return scroll

    def _add_page(self, page):
        self.pages.addWidget(self._scrollable(page))

    def _show_page(self, index):
        self.pages.setCurrentIndex(index)
        for page_index, button in enumerate(self.nav_buttons):
            button.setChecked(page_index == index)

    def _build_create_page(self):
        page, outer = self._page(
            "Create a video",
            "Turn your local clip library into a narrated, captioned short. Nothing is uploaded automatically.",
        )
        card, form = self._card(
            "Video setup",
            "Choose the topic and format. Your imported clips provide the visuals.",
        )
        fields = QFormLayout()
        fields.setHorizontalSpacing(18)
        fields.setVerticalSpacing(12)
        self.story_niche = QLineEdit()
        self.story_niche.setPlaceholderText("e.g. beginner home gardening")
        self.story_duration = QSpinBox()
        self.story_duration.setRange(20, 90)
        self.story_duration.setValue(30)
        self.story_duration.setFixedWidth(140)
        self.orientation = QComboBox()
        self.orientation.addItems(("portrait", "landscape", "square"))
        self.orientation.setFixedWidth(180)
        fields.addRow("Story topic", self.story_niche)
        fields.addRow("Target length", self.story_duration)
        fields.addRow("Output format", self.orientation)
        form.addLayout(fields)

        clip_row = QHBoxLayout()
        clip_label = QLabel(
            "Managed clip library: data\\clips (imports are copied here)"
        )
        clip_label.setObjectName("muted")
        clip_row.addWidget(clip_label, 1)
        self.ingest_button = QPushButton("Import local or synced Drive folder")
        self.ingest_button.clicked.connect(self._choose_clip_folder)
        clip_row.addWidget(self.ingest_button)
        self.drive_import_button = QPushButton("Import from cloud Drive")
        self.drive_import_button.clicked.connect(self._choose_drive_folder)
        clip_row.addWidget(self.drive_import_button)
        form.addLayout(clip_row)

        self.daily_limit_label = QLabel("Daily limit: checking...")
        self.daily_limit_label.setObjectName("muted")
        form.addWidget(self.daily_limit_label)

        self.generate_button = QPushButton("Generate local video")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self._start_generation)
        form.addWidget(self.generate_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        form.addWidget(self.progress)
        self.status = QLabel("Ready when you are.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        self.job_id_status = QLabel("")
        self.job_id_status.setObjectName("muted")
        form.addWidget(self.job_id_status)

        recent_card, recent = self._card("Recent videos")
        recent_actions = QHBoxLayout()
        self.open_video_button = QPushButton("Open latest video")
        self.open_video_button.setEnabled(False)
        self.open_video_button.clicked.connect(self._open_video)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._refresh_recent_outputs)
        self.open_selected_button = QPushButton("Open selected")
        self.open_selected_button.setEnabled(False)
        self.open_selected_button.clicked.connect(self._open_selected_output)
        recent_actions.addWidget(self.open_video_button)
        recent_actions.addWidget(self.open_selected_button)
        recent_actions.addStretch(1)
        recent_actions.addWidget(refresh_button)
        recent.addLayout(recent_actions)
        self.recent_outputs = QListWidget()
        self.recent_outputs.setMinimumHeight(110)
        self.recent_outputs.itemSelectionChanged.connect(self._recent_output_selected)
        self.recent_outputs.itemDoubleClicked.connect(
            lambda _item: self._open_selected_output()
        )
        recent.addWidget(self.recent_outputs)
        outer.addWidget(card)
        outer.addWidget(recent_card, 1)
        self._add_page(page)

    def _build_settings_page(self):
        page, outer = self._page(
            "AI settings",
            "Scan this PC once, pick a local Ollama model that fits the hardware, and save. Those choices stay in your local .env file. Offline speech and local research stay available without a cloud AI connection.",
        )
        device_card, device = self._card(
            "This PC",
            "Detect RAM, GPU, FFmpeg, TTS, and Ollama models on this machine. Compatible models are listed below. Save once and the app keeps using them.",
        )
        self.pc_scan_status = QPlainTextEdit()
        self.pc_scan_status.setReadOnly(True)
        self.pc_scan_status.setMinimumHeight(170)
        self.pc_scan_status.setPlainText("Click Scan this PC to check whether Enlivonex can run here.")
        device.addWidget(self.pc_scan_status)
        device_buttons = QHBoxLayout()
        self.scan_pc_button = QPushButton("Scan this PC")
        self.scan_pc_button.clicked.connect(self._scan_this_pc)
        self.install_runtime_button = QPushButton("Install missing components")
        self.install_runtime_button.clicked.connect(self._install_local_runtime)
        self.apply_recommended_button = QPushButton("Use recommended models")
        self.apply_recommended_button.clicked.connect(self._apply_recommended_models)
        self.refresh_models_button = QPushButton("Refresh installed models")
        self.refresh_models_button.clicked.connect(self._refresh_ollama_models)
        device_buttons.addWidget(self.scan_pc_button)
        device_buttons.addWidget(self.install_runtime_button)
        device_buttons.addWidget(self.apply_recommended_button)
        device_buttons.addWidget(self.refresh_models_button)
        device_buttons.addStretch(1)
        device.addLayout(device_buttons)

        card, form = self._card("Story generation")
        self.story_provider = QComboBox()
        self.story_provider.addItems(("mock", "openai", "anthropic", "gemini", "ollama"))
        self.openai_entry = QLineEdit()
        self.openai_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_entry = QLineEdit()
        self.anthropic_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_entry = QLineEdit()
        self.gemini_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_embedding_model = QComboBox()
        self.ollama_embedding_model.setEditable(True)
        self.ollama_base_url = QLineEdit()
        self.web_research_enabled = QCheckBox(
            "Enable online web research (requires internet)"
        )
        form_layout = QFormLayout()
        form_layout.setHorizontalSpacing(18)
        form_layout.setVerticalSpacing(10)
        form_layout.addRow("Story provider", self.story_provider)
        form_layout.addRow("OpenAI API key", self.openai_entry)
        form_layout.addRow("Anthropic API key", self.anthropic_entry)
        form_layout.addRow("Gemini API key", self.gemini_entry)
        form_layout.addRow("Ollama model", self.ollama_model)
        form_layout.addRow("Ollama embedding model", self.ollama_embedding_model)
        form_layout.addRow("Ollama URL", self.ollama_base_url)
        form_layout.addRow("", self.web_research_enabled)
        form.addLayout(form_layout)

        speech_card, speech = self._card("Narration and research")
        self.tts_provider = QComboBox()
        self.tts_provider.addItems(("offline", "openai", "mock"))
        speech_form = QFormLayout()
        speech_form.addRow("Narration provider", self.tts_provider)
        speech.addLayout(speech_form)
        research_row = QHBoxLayout()
        research_note = QLabel("Index .txt, .md, or .pdf files for local retrieval.")
        research_note.setObjectName("muted")
        research_row.addWidget(research_note, 1)
        self.research_docs_button = QPushButton("Choose documents folder")
        self.research_docs_button.clicked.connect(self._choose_research_folder)
        research_row.addWidget(self.research_docs_button)
        speech.addLayout(research_row)

        admin_card, admin = self._card(
            "Administrator access",
            "Testing phase limits regular accounts to 2 videos per day. Enter the Administrator Key to unlock unlimited creation.",
        )
        admin_row = QHBoxLayout()
        self.admin_key_entry = QLineEdit()
        self.admin_key_entry.setPlaceholderText("Enter Administrator Key")
        self.admin_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.activate_admin_button = QPushButton("Unlock administrator mode")
        self.activate_admin_button.clicked.connect(self._activate_admin_key)
        admin_row.addWidget(self.admin_key_entry, 1)
        admin_row.addWidget(self.activate_admin_button)
        admin.addLayout(admin_row)
        self.admin_status_label = QLabel("Checking administrator status...")
        self.admin_status_label.setWordWrap(True)
        admin.addWidget(self.admin_status_label)

        save_button = QPushButton("Save local settings")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._save_settings)
        outer.addWidget(device_card)
        outer.addWidget(card)
        outer.addWidget(speech_card)
        outer.addWidget(admin_card)
        outer.addWidget(save_button, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)
        self._add_page(page)

    def _build_youtube_page(self):
        page, outer = self._page(
            "YouTube studio",
            "Connect a channel, review the video and metadata, then confirm each manual upload.",
        )
        accounts_card, accounts = self._card("Channel accounts")
        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("Upload account"))
        self.youtube_account_combo = QComboBox()
        self.youtube_account_combo.currentTextChanged.connect(
            self._select_youtube_account
        )
        account_row.addWidget(self.youtube_account_combo, 1)
        self.connect_youtube_button = QPushButton("Add / connect account")
        self.connect_youtube_button.clicked.connect(self._connect_youtube)
        account_row.addWidget(self.connect_youtube_button)
        self.remove_account_button = QPushButton("Remove")
        self.remove_account_button.clicked.connect(self._remove_youtube_account)
        account_row.addWidget(self.remove_account_button)
        accounts.addLayout(account_row)
        self.yt_status_label = QLabel("Checking YouTube setup...")
        self.yt_status_label.setObjectName("status")
        self.yt_status_label.setWordWrap(True)
        accounts.addWidget(self.yt_status_label)

        upload_card, upload = self._card("Manual upload")
        path_row = QHBoxLayout()
        self.upload_path_label = QLabel("No video selected")
        self.upload_path_label.setObjectName("muted")
        self.upload_path_label.setWordWrap(True)
        path_row.addWidget(self.upload_path_label, 1)
        choose_video = QPushButton("Choose MP4")
        choose_video.clicked.connect(self._choose_upload_video)
        path_row.addWidget(choose_video)
        use_recent = QPushButton("Use selected recent video")
        use_recent.clicked.connect(self._use_selected_recent_output)
        path_row.addWidget(use_recent)
        upload.addLayout(path_row)

        self.upload_title = QLineEdit()
        self.upload_title.setMaxLength(100)
        self.upload_description = QPlainTextEdit()
        self.upload_description.setMinimumHeight(100)
        self.upload_hashtags = QLineEdit()
        self.upload_hashtags.setPlaceholderText("shorts, example")
        self.upload_visibility = QComboBox()
        self.upload_visibility.addItems(("private", "unlisted", "public"))
        self.upload_category = QComboBox()
        self.upload_category.addItems(
            ("Gaming (20)", "People & Blogs (22)", "Entertainment (24)", "Education (27)")
        )
        self.upload_kids = QCheckBox("This video is made for kids")
        metadata_form = QFormLayout()
        metadata_form.setHorizontalSpacing(18)
        metadata_form.setVerticalSpacing(9)
        metadata_form.addRow("Title", self.upload_title)
        metadata_form.addRow("Description", self.upload_description)
        metadata_form.addRow("Hashtags", self.upload_hashtags)
        metadata_form.addRow("Visibility", self.upload_visibility)
        metadata_form.addRow("Category", self.upload_category)
        metadata_form.addRow("", self.upload_kids)
        upload.addLayout(metadata_form)

        self.upload_limit_label = QLabel("Daily account uploads: checking...")
        self.upload_limit_label.setObjectName("muted")
        upload.addWidget(self.upload_limit_label)

        upload_buttons = QHBoxLayout()
        self.generate_upload_metadata_button = QPushButton("Generate metadata with AI")
        self.generate_upload_metadata_button.clicked.connect(self._generate_upload_metadata)
        self.upload_button = QPushButton("Review and upload")
        self.upload_button.setObjectName("primary")
        self.upload_button.setEnabled(False)
        self.upload_button.clicked.connect(self._start_youtube_upload)
        upload_buttons.addWidget(self.generate_upload_metadata_button)
        upload_buttons.addStretch(1)
        upload_buttons.addWidget(self.upload_button)
        upload.addLayout(upload_buttons)
        outer.addWidget(accounts_card)
        outer.addWidget(upload_card, 1)
        self._add_page(page)

    def _build_repurpose_page(self):
        self.repurpose_page = LocalClipperPage(env_file=ENV_FILE)
        self.repurpose_page.prepare_upload_requested.connect(
            self._prepare_repurpose_upload
        )
        self._add_page(self.repurpose_page)

    def closeEvent(self, event):
        if self.repurpose_page.is_busy:
            QMessageBox.information(
                self,
                "Local task in progress",
                "Wait for local analysis or rendering to finish before closing "
                "the app. Source files remain unchanged.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _load_settings(self):
        settings = load_settings()
        self.story_provider.setCurrentText(settings.story_provider)
        self.tts_provider.setCurrentText(settings.tts_provider)
        self._set_model_combo(self.ollama_model, settings.ollama_model)
        self._set_model_combo(self.ollama_embedding_model, settings.ollama_embedding_model)
        self.ollama_base_url.setText(settings.ollama_base_url)
        self.web_research_enabled.setChecked(settings.web_research_enabled)
        self.openai_entry.setText(settings.openai_api_key)
        self.anthropic_entry.setText(settings.anthropic_api_key)
        self.gemini_entry.setText(settings.gemini_api_key)
        if hasattr(self, "admin_key_entry"):
            self.admin_key_entry.setText(getattr(settings, "admin_key", ""))
        self._refresh_limits_status()

    def _save_settings(self):
        try:
            set_key(str(ENV_FILE), "STORY_PROVIDER", self.story_provider.currentText())
            set_key(str(ENV_FILE), "OPENAI_API_KEY", self.openai_entry.text())
            set_key(str(ENV_FILE), "ANTHROPIC_API_KEY", self.anthropic_entry.text())
            set_key(str(ENV_FILE), "GEMINI_API_KEY", self.gemini_entry.text())
            set_key(str(ENV_FILE), "OLLAMA_MODEL", self.ollama_model.currentText().strip())
            set_key(
                str(ENV_FILE),
                "OLLAMA_EMBEDDING_MODEL",
                self.ollama_embedding_model.currentText().strip(),
            )
            set_key(str(ENV_FILE), "OLLAMA_BASE_URL", self.ollama_base_url.text().strip())
            set_key(
                str(ENV_FILE),
                "WEB_RESEARCH_ENABLED",
                str(self.web_research_enabled.isChecked()).lower(),
            )
            set_key(str(ENV_FILE), "TTS_PROVIDER", self.tts_provider.currentText())
            if hasattr(self, "admin_key_entry"):
                set_key(str(ENV_FILE), "ADMIN_KEY", self.admin_key_entry.text().strip())
            load_dotenv(ENV_FILE, override=True)
            self._persist_device_profile()
            self._refresh_limits_status()
            QMessageBox.information(
                self,
                "Settings saved",
                "Local AI settings were saved for this PC and will be reused on the next launch.",
            )
        except OSError as error:
            QMessageBox.critical(self, "Settings error", f"Could not save settings: {error}")

    @staticmethod
    def _set_model_combo(combo, value, extras=None):
        extras = extras or []
        existing = {combo.itemText(index) for index in range(combo.count())}
        for item in list(extras) + ([value] if value else []):
            if item and item not in existing:
                combo.addItem(item)
                existing.add(item)
        if value:
            combo.setCurrentText(value)

    def _persist_device_profile(self):
        from app.system.profile import save_profile

        save_profile(
            hardware=getattr(self._last_scan, "hardware", None) if self._last_scan else None,
            story_provider=self.story_provider.currentText(),
            ollama_model=self.ollama_model.currentText().strip(),
            ollama_embedding_model=self.ollama_embedding_model.currentText().strip(),
            ollama_base_url=self.ollama_base_url.text().strip(),
            tts_provider=self.tts_provider.currentText(),
        )

    def _scan_this_pc(self):
        settings = load_settings()
        self.scan_pc_button.setEnabled(False)
        self.pc_scan_status.setPlainText("Scanning this PC...")

        def work():
            from app.system.readiness import format_scan, scan_system

            try:
                scan = scan_system(
                    ffmpeg_path=settings.ffmpeg_path,
                    ffprobe_path=settings.ffprobe_path,
                    ollama_base_url=self.ollama_base_url.text().strip()
                    or settings.ollama_base_url,
                    story_model=self.ollama_model.currentText().strip()
                    or settings.ollama_model,
                    embedding_model=self.ollama_embedding_model.currentText().strip()
                    or settings.ollama_embedding_model,
                )
                self.events.put(("pc_scan_done", (scan, format_scan(scan))))
            except Exception as error:
                self.events.put(("pc_scan_failed", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _install_local_runtime(self):
        settings = load_settings()
        self.install_runtime_button.setEnabled(False)
        self.pc_scan_status.setPlainText("Installing missing local components...")

        def work():
            from app.system.bootstrap import install_local_runtime

            try:
                notes = install_local_runtime(
                    ollama_base_url=self.ollama_base_url.text().strip()
                    or settings.ollama_base_url,
                    story_model=self.ollama_model.currentText().strip()
                    or settings.ollama_model,
                    embedding_model=self.ollama_embedding_model.currentText().strip()
                    or settings.ollama_embedding_model,
                    progress=lambda message: self.events.put(("pc_install_progress", message)),
                )
                self.events.put(("pc_install_done", notes))
            except Exception as error:
                self.events.put(("pc_install_failed", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_recommended_models(self):
        if not self._last_scan:
            QMessageBox.information(
                self,
                "Scan this PC first",
                "Scan this PC so Enlivonex can recommend a model that fits the hardware.",
            )
            return
        recommendation = self._last_scan.recommendation
        self.story_provider.setCurrentText("ollama")
        self._set_model_combo(
            self.ollama_model,
            recommendation.story_model,
            recommendation.compatible_story_models + self._last_scan.installed_ollama_models,
        )
        self._set_model_combo(
            self.ollama_embedding_model,
            recommendation.embedding_model,
            self._last_scan.installed_ollama_models,
        )
        QMessageBox.information(
            self,
            "Recommended models applied",
            f"Story model set to {recommendation.story_model}. "
            "Click Save local settings to keep this choice.",
        )

    def _refresh_ollama_models(self):
        """Load locally installed Ollama models into both model selectors."""
        settings = load_settings()
        base_url = self.ollama_base_url.text().strip() or settings.ollama_base_url
        self.refresh_models_button.setEnabled(False)
        self.pc_scan_status.setPlainText("Checking locally installed Ollama models...")

        def work():
            from app.ai.ollama import OllamaClient

            try:
                client = OllamaClient(
                    base_url=base_url,
                    model=settings.ollama_model,
                )
                models = client.list_installed_models()
                self.events.put(("ollama_models_done", models))
            except Exception as error:
                self.events.put(("ollama_models_failed", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _activate_admin_key(self):
        from app.limits import verify_admin_key
        key = self.admin_key_entry.text().strip()
        if verify_admin_key(key):
            set_key(str(ENV_FILE), "ADMIN_KEY", key)
            load_dotenv(ENV_FILE, override=True)
            self._refresh_limits_status()
            QMessageBox.information(
                self,
                "Administrator Access Granted",
                "Administrator Key verified! Unlimited video creation and uploads are now unlocked.",
            )
        else:
            QMessageBox.critical(
                self,
                "Invalid Key",
                "The key you entered is incorrect. Please check the administrator key and try again.",
            )

    def _refresh_limits_status(self):
        from app.limits import check_creation_limit, check_upload_limit, is_admin_mode
        settings = load_settings()
        admin = is_admin_mode(settings=settings)

        # Update Create Video page limit label
        if hasattr(self, "daily_limit_label"):
            _, _, create_cnt = check_creation_limit(
                database_path=Path("data/media.db"),
                settings=settings,
            )
            if admin:
                self.daily_limit_label.setText("Administrator Mode: Active (Unlimited video creation)")
                self.daily_limit_label.setStyleSheet("color: #16a34a; font-weight: 600;")
            else:
                remaining = max(0, 2 - create_cnt)
                self.daily_limit_label.setText(
                    f"Daily limit: {create_cnt}/2 videos created today ({remaining} remaining)"
                )
                self.daily_limit_label.setStyleSheet("color: #425570;")

        # Update Settings page admin status label
        if hasattr(self, "admin_status_label"):
            if admin:
                self.admin_status_label.setText("Status: Administrator Mode Active (Unlimited videos & uploads)")
                self.admin_status_label.setStyleSheet("color: #16a34a; font-weight: 700;")
            else:
                self.admin_status_label.setText("Status: Standard Testing Mode (Limited to 2 videos per day)")
                self.admin_status_label.setStyleSheet("color: #425570;")

        # Update YouTube Studio page upload limit label
        if hasattr(self, "upload_limit_label"):
            account = self._selected_youtube_account()
            account_id = account.account_id if account else None
            _, _, upload_cnt = check_upload_limit(
                database_path=Path("data/media.db"),
                account_id=account_id,
                settings=settings,
            )
            if admin:
                self.upload_limit_label.setText("Administrator Mode: Active (Unlimited uploads)")
                self.upload_limit_label.setStyleSheet("color: #16a34a; font-weight: 600;")
            else:
                remaining = max(0, 2 - upload_cnt)
                self.upload_limit_label.setText(
                    f"Daily account upload limit: {upload_cnt}/2 today ({remaining} remaining)"
                )
                self.upload_limit_label.setStyleSheet("color: #425570;")

    def _choose_clip_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder containing video clips",
            "",
        )
        if not directory:
            return

        self._begin_task("Copying clips into the managed library...", indeterminate=True)

        def import_clips():
            try:
                count = self.runner.ingest_directory(Path(directory))
                self.events.put(("ingested", count))
            except Exception as error:
                self.events.put(("failed", str(error)))

        threading.Thread(target=import_clips, daemon=True).start()

    def _choose_drive_folder(self):
        settings = load_settings()
        if not settings.google_client_id or not settings.google_client_secret:
            QMessageBox.information(
                self,
                "Cloud Drive is not configured",
                "Cloud Drive import requires the app publisher's Google Drive "
                "OAuth configuration and any Google verification it requires. "
                "You can still import a Google Drive for desktop synced folder "
                "using the local folder button.",
            )
            return

        folder_id, accepted = QInputDialog.getText(
            self,
            "Import from Google Drive",
            "Enter the Google Drive folder ID:",
            QLineEdit.EchoMode.Normal,
            settings.google_drive_folder_id,
        )
        folder_id = folder_id.strip()
        if not accepted or not folder_id:
            return

        try:
            set_key(str(ENV_FILE), "GOOGLE_DRIVE_FOLDER_ID", folder_id)
            load_dotenv(ENV_FILE, override=True)
        except OSError as error:
            QMessageBox.critical(
                self, "Could not save Drive folder", str(error)
            )
            return

        self._begin_task("Connecting to Google Drive and downloading clips...", indeterminate=True)

        def import_drive_clips():
            try:
                count = self.runner.ingest_drive_folder(folder_id)
                self.events.put(("ingested", count))
            except Exception as error:
                self.events.put(("failed", str(error)))

        threading.Thread(target=import_drive_clips, daemon=True).start()

    def _choose_research_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a folder containing research documents"
        )
        if not directory:
            return

        def index_documents():
            try:
                result = self.runner.ingest_research_directory(Path(directory))
                self.events.put(("research_documents_ingested", result))
            except Exception as error:
                self.events.put(("failed", str(error)))

        self._begin_task("Indexing research documents with Ollama...", indeterminate=True)
        threading.Thread(target=index_documents, daemon=True).start()

    def _start_generation(self):
        story_duration = float(self.story_duration.value())
        story_niche = self.story_niche.text().strip()
        if not math.isfinite(story_duration) or not 20 <= story_duration <= 90 or not story_niche:
            QMessageBox.warning(
                self,
                "Video details required",
                "Enter a story topic and choose a duration between 20 and 90 seconds.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Generate local video",
            "This will run the selected story and narration providers and save a video locally. "
            "It will not upload anything to YouTube. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Check daily video creation limit (2 videos per day for test accounts)
        from app.limits import check_creation_limit, verify_admin_key
        settings = load_settings()
        allowed, message, count = check_creation_limit(
            database_path=Path("data/media.db"),
            settings=settings,
        )
        if not allowed:
            key, ok = QInputDialog.getText(
                self,
                "Daily Limit Reached (2 Videos / Day)",
                f"You have reached the test limit of 2 videos created today ({count}/2 used).\n\n"
                "If you are the Administrator, enter the Administrator Key to unlock unlimited creation:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            if verify_admin_key(key):
                set_key(str(ENV_FILE), "ADMIN_KEY", key.strip())
                load_dotenv(ENV_FILE, override=True)
                self._refresh_limits_status()
                QMessageBox.information(
                    self,
                    "Administrator Mode Activated",
                    "Administrator Key verified! Unlimited video creation is now unlocked on this machine.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Access Denied",
                    "Invalid Administrator Key. Video creation is capped at 2 videos per day for test accounts.",
                )
                return

        orientation = self.orientation.currentText()
        self.open_video_button.setEnabled(False)
        self.output_path = None
        self.job_id_status.clear()
        self._begin_task("Starting local video job...")

        current_admin_key = load_settings().admin_key

        def generate():
            try:
                result = self.runner.run_job(
                    orientation=orientation,
                    story_duration=story_duration,
                    story_niche=story_niche,
                    admin_key=current_admin_key,
                    on_job_created=lambda job_id: self.events.put(("job_created", job_id)),
                )
                self.events.put(("completed", result))
            except Exception as error:
                self.events.put(("failed", str(error)))

        threading.Thread(target=generate, daemon=True).start()

    def _begin_task(self, status, indeterminate=False, rotate_activity=True):
        self.active_task = True
        self.rotate_activity_messages = rotate_activity
        self.current_stage = None
        self.activity_message = ACTIVITY_MESSAGES[0]
        self.status.setText(status)
        self.progress.setRange(0, 0 if indeterminate else 100)
        if not indeterminate:
            self.progress.setValue(0)
        self.generate_button.setEnabled(False)
        self.ingest_button.setEnabled(False)
        self.drive_import_button.setEnabled(False)
        self.connect_youtube_button.setEnabled(False)
        self.research_docs_button.setEnabled(False)
        self.generate_upload_metadata_button.setEnabled(False)
        self._update_upload_button_state()
        if rotate_activity:
            self.activity_timer.start(random.randint(5000, 8000))

    def _rotate_activity_message(self):
        if not self.active_task or not self.rotate_activity_messages:
            self.activity_timer.stop()
            return
        self.activity_message = random.choice(
            tuple(message for message in ACTIVITY_MESSAGES if message != self.activity_message)
        )
        stage = f"  |  {self.current_stage.replace('_', ' ').title()}" if self.current_stage else ""
        self.status.setText(f"{self.activity_message}{stage}")
        self.activity_timer.start(random.randint(5000, 8000))

    def _finish_task(self, completed=False):
        self.active_task = False
        self.active_job_id = None
        self.current_stage = None
        self.activity_timer.stop()
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        if completed:
            self.progress.setValue(100)
        self.generate_button.setEnabled(True)
        self.ingest_button.setEnabled(True)
        self.drive_import_button.setEnabled(True)
        self.connect_youtube_button.setEnabled(True)
        self.research_docs_button.setEnabled(True)
        self._update_upload_button_state()

    def _process_events(self):
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "job_created":
                self.active_job_id = payload
                self.job_id_status.setText(f"Job: {payload}")
            elif event == "ingested":
                self._finish_task(completed=True)
                self.status.setText(f"Imported {payload} new clip(s).")
            elif event == "research_documents_ingested":
                self._finish_task(completed=True)
                message = (
                    f"Indexed {payload['files']} research document(s) "
                    f"into {payload['chunks']} searchable chunks."
                )
                if payload.get("warnings"):
                    message += f" Ollama unload warning: {payload['warnings'][0]}"
                self.status.setText(message)
            elif event == "completed":
                self._finish_task(completed=True)
                self.output_path = payload.get("output_path")
                self._refresh_recent_outputs()
                self._refresh_limits_status()
                target_duration = payload.get("target_story_duration")
                narration_duration = payload.get("narration_duration")
                duration_summary = ""
                if narration_duration is not None and target_duration is not None:
                    duration_summary = (
                        f" Final video: {narration_duration:.1f}s "
                        f"(requested {target_duration:.0f}s; narration was not cut)."
                    )
                    if not payload.get("duration_within_tolerance", True):
                        duration_summary += " This is outside the requested +/-3.5s tolerance."
                status = (
                    f"Completed: {payload['story_title']}.{duration_summary} "
                    f"Job artifacts: {payload['artifacts_dir']}"
                )
                if payload.get("warnings"):
                    status += f" Warning: {payload['warnings'][0]}"
                if payload.get("research_warning"):
                    status += f" {payload['research_warning']}"
                if self.output_path:
                    self.open_video_button.setEnabled(True)
                    self.status.setText(status)
                else:
                    self.status.setText(
                        "Story job completed; no video was produced. "
                        f"Artifacts: {payload['artifacts_dir']}"
                    )
            elif event == "failed":
                self._finish_task()
                self.status.setText("Job failed.")
                QMessageBox.critical(self, "Automation failed", payload)
            elif event == "youtube_connected":
                self._finish_task()
                self._update_youtube_status()
                QMessageBox.information(
                    self, "YouTube connected", f"'{payload}' is ready for uploads."
                )
            elif event == "youtube_uploaded":
                self._finish_task(completed=True)
                self.youtube_id = payload["video_id"]
                self._update_youtube_status()
                self._refresh_limits_status()
                QMessageBox.information(
                    self, "Upload complete", f"Video uploaded successfully:\n{payload['url']}"
                )
            elif event == "youtube_progress":
                if self.progress.maximum() == 0:
                    self.progress.setRange(0, 100)
                self.progress.setValue(payload)
                self.status.setText(f"Uploading to YouTube... {payload}%")
            elif event == "youtube_upload_started":
                self.status.setText("Uploading to YouTube... waiting for first chunk")
            elif event == "upload_metadata_generated":
                metadata = payload
                self.upload_title.setText(metadata.title)
                self.upload_description.setPlainText(metadata.description)
                self.upload_hashtags.setText(", ".join(metadata.hashtags))
                self._finish_task(completed=True)
                QMessageBox.information(
                    self,
                    "Metadata generated",
                    "Review the generated title, description, and hashtags before upload.",
                )
            elif event == "ollama_unload_warning":
                QMessageBox.warning(self, "Ollama cleanup warning", payload)
            elif event == "youtube_failed":
                self._finish_task()
                self._update_youtube_status()
                QMessageBox.critical(self, "YouTube action failed", payload)
            elif event == "pc_scan_done":
                scan, text = payload
                self._last_scan = scan
                self.scan_pc_button.setEnabled(True)
                self.install_runtime_button.setEnabled(True)
                self.pc_scan_status.setPlainText(text)
                extras = scan.recommendation.compatible_story_models + scan.installed_ollama_models
                self._set_model_combo(
                    self.ollama_model,
                    self.ollama_model.currentText() or scan.recommendation.story_model,
                    extras,
                )
                self._set_model_combo(
                    self.ollama_embedding_model,
                    self.ollama_embedding_model.currentText()
                    or scan.recommendation.embedding_model,
                    scan.installed_ollama_models,
                )
            elif event == "pc_scan_failed":
                self.scan_pc_button.setEnabled(True)
                self.pc_scan_status.setPlainText(f"Scan failed: {payload}")
            elif event == "pc_install_progress":
                self.pc_scan_status.appendPlainText(payload)
            elif event == "pc_install_done":
                self.install_runtime_button.setEnabled(True)
                self.pc_scan_status.appendPlainText("\n".join(payload))
                self._scan_this_pc()
            elif event == "pc_install_failed":
                self.install_runtime_button.setEnabled(True)
                self.pc_scan_status.appendPlainText(f"Install failed: {payload}")
            elif event == "ollama_models_done":
                self.refresh_models_button.setEnabled(True)
                models = payload
                if models:
                    self._set_model_combo(
                        self.ollama_model, self.ollama_model.currentText(), models
                    )
                    self._set_model_combo(
                        self.ollama_embedding_model,
                        self.ollama_embedding_model.currentText(),
                        models,
                    )
                    self.pc_scan_status.setPlainText(
                        "Installed local Ollama models:\n" + "\n".join(models)
                    )
                else:
                    self.pc_scan_status.setPlainText(
                        "Ollama is running, but no local models are installed yet."
                    )
            elif event == "ollama_models_failed":
                self.refresh_models_button.setEnabled(True)
                self.pc_scan_status.setPlainText(
                    f"Could not load Ollama models: {payload}"
                )

        if self.active_task and self.active_job_id:
            try:
                stage = self.runner.get_job_stage(self.active_job_id)
                if stage and stage != self.current_stage:
                    self.current_stage = stage
                    progress = JOB_PROGRESS.get(stage)
                    if progress is not None and self.progress.maximum() > 0:
                        self.progress.setValue(max(self.progress.value(), progress))
                    status = (
                        f"{self.activity_message}  |  {stage.replace('_', ' ').title()}"
                    )
                    if stage == "GENERATING_STORY":
                        status += f" (provider timeout: {GEMINI_REQUEST_TIMEOUT_SECONDS}s)"
                    self.status.setText(status)
            except sqlite3.Error as error:
                self.status.setText(f"Could not read job status: {error}")

    def _refresh_recent_outputs(self):
        self.recent_outputs.clear()
        self.recent_output_paths = self.runner.get_recent_outputs()
        for path in self.recent_output_paths:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            item = QListWidgetItem(
                f"{timestamp}   |   {path.parent.name}   |   {path.name}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.recent_outputs.addItem(item)
        self.open_selected_button.setEnabled(bool(self.recent_output_paths))

    def _open_video(self):
        if self.output_path:
            self._open_output_path(Path(self.output_path))

    def _open_selected_output(self):
        item = self.recent_outputs.currentItem()
        if item is None:
            QMessageBox.information(self, "Select a video", "Choose a video from the recent list.")
            return
        self._open_output_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _open_output_path(self, path):
        path = Path(path)
        if not path.exists():
            QMessageBox.critical(self, "Video not found", f"Generated video is missing: {path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.critical(self, "Could not open video", f"Could not open: {path}")

    def _update_youtube_status(self):
        self._refresh_youtube_accounts()
        settings = load_settings()
        account = self._selected_youtube_account()
        if account and self.youtube_accounts.token_path(account.account_id).is_file():
            status = f"Connected account: {account.label}"
        elif settings.youtube_client_secret_file.exists():
            status = "OAuth credentials ready - connect required"
        else:
            status = (
                "YouTube sign-in is not configured in this app. Contact the app "
                "publisher; users should not need to create a Google Cloud project."
            )
        self.yt_status_label.setText(status)

    def _refresh_youtube_accounts(self):
        accounts = self.youtube_accounts.accounts()
        self._account_labels = {account.label: account for account in accounts}
        labels = list(self._account_labels)
        active = self.youtube_accounts.active_account_id
        selected = next(
            (account.label for account in accounts if account.account_id == active),
            labels[0] if labels else "",
        )
        self.youtube_account_combo.blockSignals(True)
        self.youtube_account_combo.clear()
        self.youtube_account_combo.addItems(labels)
        if selected:
            self.youtube_account_combo.setCurrentText(selected)
        self.youtube_account_combo.blockSignals(False)
        self._update_upload_button_state()

    def _selected_youtube_account(self):
        return self._account_labels.get(self.youtube_account_combo.currentText())

    def _select_youtube_account(self, _label=None):
        account = self._selected_youtube_account()
        if account:
            try:
                self.youtube_accounts.set_active_account(account.account_id)
            except (OSError, RuntimeError, ValueError) as error:
                QMessageBox.critical(self, "Account selection failed", str(error))
        self._update_upload_button_state()
        self._refresh_limits_status()

    def _remove_youtube_account(self):
        account = self._selected_youtube_account()
        if not account:
            QMessageBox.information(self, "No account selected", "Connect an account first.")
            return
        answer = QMessageBox.question(
            self,
            "Remove YouTube account",
            f"Remove '{account.label}' and delete its locally saved sign-in token?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.youtube_accounts.remove_account(account.account_id)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(self, "Account removal failed", str(error))
            return
        self._update_youtube_status()

    def _recent_output_selected(self):
        item = self.recent_outputs.currentItem()
        if item:
            self._select_upload_video(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _use_selected_recent_output(self):
        item = self.recent_outputs.currentItem()
        if not item:
            QMessageBox.information(
                self,
                "Select a video",
                "Select a video in the Create video page first, or choose a video file.",
            )
            return
        self._select_upload_video(Path(item.data(Qt.ItemDataRole.UserRole)))
        self._show_page(2)

    def _choose_upload_video(self):
        selected, _file_type = QFileDialog.getOpenFileName(
            self,
            "Choose a video to upload",
            "",
            "MP4 video (*.mp4);;All files (*.*)",
        )
        if selected:
            self._select_upload_video(Path(selected))

    def _select_upload_video(self, path, *, load_metadata=True):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() != ".mp4":
            QMessageBox.critical(self, "Invalid video", "Choose an existing MP4 video file.")
            return
        self.upload_path = path
        self.upload_path_label.setText(str(path))
        self.upload_title.setText(path.stem[:100])
        self.upload_description.clear()
        self.upload_hashtags.clear()
        self.upload_visibility.setCurrentText("private")
        self.upload_category.setCurrentText("People & Blogs (22)")
        self.upload_kids.setChecked(False)

        metadata_path = path.parent / "metadata.json"
        if load_metadata and metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                snippet = metadata.get("snippet", {})
                self.upload_title.setText(snippet.get("title", path.stem)[:100])
                description = snippet.get("description", "")
                description_lines = description.splitlines()
                if (
                    description_lines
                    and description_lines[-1].split()
                    and all(word.startswith("#") for word in description_lines[-1].split())
                ):
                    self.upload_hashtags.setText(
                        ", ".join(
                            word[1:]
                            for word in description_lines[-1].split()
                            if word.startswith("#")
                        )
                    )
                    description = "\n".join(description_lines[:-1]).rstrip()
                self.upload_description.setPlainText(description)
                status = metadata.get("status", {})
                self.upload_visibility.setCurrentText(
                    status.get("privacyStatus", "private")
                )
                self.upload_kids.setChecked(
                    bool(status.get("selfDeclaredMadeForKids", False))
                )
                category_id = str(snippet.get("categoryId", "20"))
                category_labels = {
                    "20": "Gaming (20)",
                    "22": "People & Blogs (22)",
                    "24": "Entertainment (24)",
                    "27": "Education (27)",
                }
                self.upload_category.setCurrentText(
                    category_labels.get(category_id, "People & Blogs (22)")
                )
            except (OSError, ValueError, AttributeError) as error:
                QMessageBox.warning(
                    self,
                    "Metadata not loaded",
                    f"Could not read generated metadata; enter it manually. ({error})",
                )
        self._update_upload_button_state()

    def _prepare_repurpose_upload(self, output_path):
        if self.active_task:
            QMessageBox.information(
                self,
                "Another task is running",
                "Wait for the current task to finish before preparing an upload.",
            )
            return
        path = Path(output_path)
        if not path.is_file() or path.suffix.lower() != ".mp4":
            QMessageBox.critical(
                self, "Invalid video", "The rendered draft is not an existing MP4."
            )
            return
        self._select_upload_video(path, load_metadata=False)
        self._show_page(2)

    def _generate_upload_metadata(self):
        if self.active_task:
            return
        if not self.upload_path or not self.upload_path.is_file():
            QMessageBox.information(self, "Select a video", "Choose an MP4 first.")
            return
        path = self.upload_path
        fallback_description = self.upload_description.toPlainText().strip()
        self._begin_task(
            "Generating metadata with the selected AI...",
            indeterminate=True,
            rotate_activity=False,
        )

        def generate():
            ollama_client = None
            try:
                settings = load_settings()
                if settings.story_provider == "ollama":
                    from app.ai.ollama import OllamaClient

                    ollama_client = OllamaClient(
                        base_url=settings.ollama_base_url,
                        model=settings.ollama_model,
                        timeout_seconds=settings.ollama_timeout_seconds,
                    )
                story_path = path.parent / "story.json"
                if story_path.is_file():
                    story = Story.from_dict(
                        json.loads(story_path.read_text(encoding="utf-8"))
                    )
                else:
                    title = path.stem
                    description = fallback_description
                    text = description or title
                    story = Story(
                        title=title,
                        hook=text,
                        segments=[StorySegment(text=text, start_time=0.0, end_time=1.0)],
                        total_duration=1.0,
                        tone="casual",
                    )
                metadata = create_metadata_generator(
                    settings, ollama_client=ollama_client
                ).generate_metadata(story)
                self.events.put(("upload_metadata_generated", metadata))
            except Exception as error:
                self.events.put(("youtube_failed", str(error)))
            finally:
                if ollama_client is not None:
                    try:
                        ollama_client.unload()
                    except Exception as error:
                        self.events.put(
                            (
                                "ollama_unload_warning",
                                f"Metadata task ended, but Ollama could not confirm "
                                f"model unload: {error}",
                            )
                        )

        threading.Thread(target=generate, daemon=True).start()

    def _update_upload_button_state(self):
        ready = self.upload_path is not None and self.upload_path.is_file() and not self.active_task
        if ready:
            settings = load_settings()
            account = self._selected_youtube_account()
            ready = (
                settings.youtube_client_secret_file.is_file()
                and account is not None
                and self.youtube_accounts.token_path(account.account_id).is_file()
            )
        self.upload_button.setEnabled(ready)
        self.generate_upload_metadata_button.setEnabled(
            not self.active_task and self.upload_path is not None
        )

    def _start_youtube_upload(self):
        if not self.upload_path or not self.upload_path.is_file():
            QMessageBox.critical(self, "Video required", "Choose an existing MP4 first.")
            return
        title = self.upload_title.text().strip()
        description = self.upload_description.toPlainText().strip()
        hashtags = [
            tag.strip().lstrip("#").replace(" ", "")
            for tag in self.upload_hashtags.text().split(",")
            if tag.strip().lstrip("#")
        ]
        visibility = self.upload_visibility.currentText()
        if not title or len(title) > 100:
            QMessageBox.warning(self, "Invalid title", "Enter a title between 1 and 100 characters.")
            return
        if visibility not in ("private", "unlisted", "public"):
            QMessageBox.warning(self, "Invalid visibility", "Choose a valid visibility.")
            return
        category_id = self.upload_category.currentText().rsplit("(", 1)[-1].rstrip(")")
        if category_id not in {"20", "22", "24", "27"}:
            QMessageBox.warning(self, "Invalid category", "Choose a valid video category.")
            return
        metadata = YouTubeMetadata(
            title=title,
            description=description,
            hashtags=hashtags[:10],
            category=category_id,
            visibility=visibility,
            made_for_kids=self.upload_kids.isChecked(),
        )
        answer = QMessageBox.question(
            self,
            "Confirm YouTube upload",
            f"Upload this video to YouTube?\n\nTitle: {metadata.title}\n"
            f"Visibility: {metadata.visibility}\nFile: {self.upload_path}\n\n"
            "Confirm you have permission to publish this video and included material. "
            "This will upload the file and apply the selected visibility.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        upload_path = self.upload_path
        account = self._selected_youtube_account()
        if not account:
            QMessageBox.critical(
                self, "YouTube account required", "Add and connect an account before uploading."
            )
            return

        # Check daily account upload limit (2 videos per day per account for test users)
        from app.limits import check_upload_limit, verify_admin_key
        settings = load_settings()
        allowed, message, count = check_upload_limit(
            database_path=Path("data/media.db"),
            account_id=account.account_id,
            settings=settings,
        )
        if not allowed:
            key, ok = QInputDialog.getText(
                self,
                "Daily Upload Limit Reached",
                f"You have reached the limit of 2 uploads for '{account.label}' today ({count}/2 used).\n\n"
                "If you are the Administrator, enter the Administrator Key to unlock unlimited uploads:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            if verify_admin_key(key):
                set_key(str(ENV_FILE), "ADMIN_KEY", key.strip())
                load_dotenv(ENV_FILE, override=True)
                self._refresh_limits_status()
                QMessageBox.information(
                    self,
                    "Administrator Mode Activated",
                    "Administrator Key verified! Unlimited uploads unlocked.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Access Denied",
                    "Invalid Administrator Key. Uploads are capped at 2 videos per day per account.",
                )
                return

        self._begin_task(
            "Authenticating and starting YouTube upload...",
            indeterminate=True,
            rotate_activity=False,
        )

        current_admin_key = load_settings().admin_key

        def upload():
            try:
                settings = load_settings()
                database = Database(Path("data/media.db"))
                uploader = YouTubeUploader(
                    db=database,
                    credentials_path=settings.youtube_client_secret_file,
                    token_path=self.youtube_accounts.token_path(account.account_id),
                )
                uploader.authenticate(allow_new_authorization=False)
                upload_record_id = upload_job_id(upload_path)
                with database:
                    database.initialize()
                    self.events.put(("youtube_upload_started", None))
                    video_id = uploader.upload_video(
                        upload_path,
                        metadata,
                        upload_record_id,
                        account_id=account.account_id,
                        admin_key=current_admin_key,
                        progress_callback=lambda percent: self.events.put(
                            ("youtube_progress", percent)
                        ),
                    )
                self.events.put(
                    (
                        "youtube_uploaded",
                        {"video_id": video_id, "url": uploader.get_video_url(video_id)},
                    )
                )
            except Exception as error:
                self.events.put(("youtube_failed", str(error)))

        threading.Thread(target=upload, daemon=True).start()

    def _connect_youtube(self):
        settings = load_settings()
        if not settings.youtube_client_secret_file.exists():
            QMessageBox.critical(
                self,
                "OAuth credentials required",
                "YouTube sign-in is not configured in this app. Users should not need "
                "to create a Google Cloud project. Contact the app publisher.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Connect YouTube",
            "A browser window will open. Choose the Google account for this YouTube "
            "channel and approve upload access. You do not need to create a Google "
            "Cloud project. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        label, accepted = self._ask_account_name()
        if not accepted:
            return
        try:
            account = self.youtube_accounts.add_account(label)
        except ValueError as error:
            QMessageBox.critical(self, "Invalid account name", str(error))
            return
        self._refresh_youtube_accounts()

        def authenticate():
            try:
                database = Database(Path("data/media.db"))
                uploader = YouTubeUploader(
                    db=database,
                    credentials_path=settings.youtube_client_secret_file,
                    token_path=self.youtube_accounts.token_path(account.account_id),
                )
                uploader.authenticate()
                self.events.put(("youtube_connected", account.label))
            except Exception as error:
                token_path = self.youtube_accounts.token_path(account.account_id)
                if not token_path.exists():
                    try:
                        self.youtube_accounts.remove_account(account.account_id)
                    except (OSError, RuntimeError, ValueError):
                        pass
                self.events.put(("youtube_failed", str(error)))

        self._begin_task("Connecting YouTube channel...", indeterminate=True)
        threading.Thread(target=authenticate, daemon=True).start()

    def _ask_account_name(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Name YouTube account")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Enter a name to identify this channel:"))
        entry = QLineEdit()
        entry.setPlaceholderText("e.g. My channel")
        layout.addWidget(entry)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return entry.text().strip(), accepted and bool(entry.text().strip())


def main():
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("Enlivonex YouTube Automation")
    if APP_ICON_PATH.is_file():
        application.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    application.setFont(QFont("Segoe UI", 10))
    window = PySideDashboard()
    window.show()
    application.exec()


if __name__ == "__main__":
    main()
