"""Tkinter desktop interface for local video generation."""

import os
import math
import queue
import random
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from dotenv import load_dotenv, set_key

from app.config.settings import load_settings
from app.database.db import Database
from app.gui_helpers import ACTIVITY_MESSAGES, JOB_PROGRESS, upload_job_id
from app.metadata.generator import create_metadata_generator
from app.metadata.models import YouTubeMetadata
from app.orchestration.runner import LocalJobRunner
from app.story.provider import GEMINI_REQUEST_TIMEOUT_SECONDS
from app.story.models import Story, StorySegment
from app.youtube.uploader import YouTubeUploader
from app.youtube.accounts import YouTubeAccountStore

ENV_FILE = Path(".env")


class DashboardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Enlivonex YouTube Automation")
        self.geometry("980x820")
        self.minsize(820, 700)
        self.configure(background="#F3F6FB")
        self._configure_theme()

        if not ENV_FILE.exists():
            ENV_FILE.touch()
        load_dotenv(ENV_FILE, override=True)
        settings = load_settings()
        self.youtube_accounts = YouTubeAccountStore(
            Path("data/youtube_accounts"),
            legacy_token_path=settings.youtube_token_file,
        )
        self.youtube_accounts.migrate_legacy_token()
        self._account_labels = {}

        self.runner = LocalJobRunner()
        self.events = queue.Queue()
        self.active_task = False
        self.rotate_activity_messages = True
        self.active_job_id = None
        self.current_stage = None
        self.activity_message = ACTIVITY_MESSAGES[0]
        self.activity_after_id = None
        self.rotate_activity_messages = True
        self.output_path = None
        self.recent_output_paths = []
        self.upload_path = None
        self.youtube_id = None

        self._create_widgets()
        self._load_settings()
        self._refresh_recent_outputs()
        self.after(250, self._process_events)

    def _create_widgets(self):
        header = ttk.Frame(self, style="BrandHeader.TFrame", padding=(24, 18))
        header.pack(fill=tk.X, padx=14, pady=(14, 8))
        brand_mark = ttk.Label(
            header,
            text="E",
            style="BrandMark.TLabel",
            anchor=tk.CENTER,
        )
        brand_mark.pack(side=tk.LEFT, padx=(0, 14))
        brand_copy = ttk.Frame(header, style="BrandHeader.TFrame")
        brand_copy.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(
            brand_copy,
            text="Enlivonex",
            style="BrandTitle.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            brand_copy,
            text="YOUR LOCAL-FIRST SHORTS STUDIO",
            style="BrandSubtitle.TLabel",
        ).pack(anchor=tk.W, pady=(3, 0))
        ttk.Label(
            header,
            text="CREATE  ·  REVIEW  ·  PUBLISH",
            style="HeaderHint.TLabel",
        ).pack(side=tk.RIGHT, padx=(12, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        self.generate_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        youtube_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.generate_tab, text="Generate Video")
        notebook.add(settings_tab, text="AI Settings")
        notebook.add(youtube_tab, text="YouTube Setup")

        self._create_generate_tab()
        self._create_settings_tab(settings_tab)
        self._create_youtube_tab(youtube_tab)

    def _configure_theme(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        background = "#F3F6FB"
        surface = "#FFFFFF"
        foreground = "#17243A"
        muted = "#61718A"
        accent = "#2563EB"
        accent_hover = "#1D4ED8"
        border = "#DCE4EF"

        style.configure(".", font=("Segoe UI", 10), foreground=foreground)
        style.configure("TFrame", background=surface)
        style.configure("TLabel", background=surface, foreground=foreground)
        style.configure(
            "BrandHeader.TFrame",
            background="#13233F",
        )
        style.configure(
            "BrandMark.TLabel",
            background=accent,
            foreground="#FFFFFF",
            font=("Segoe UI", 19, "bold"),
            width=3,
            padding=(0, 7),
        )
        style.configure(
            "BrandTitle.TLabel",
            background="#13233F",
            foreground="#FFFFFF",
            font=("Segoe UI", 19, "bold"),
        )
        style.configure(
            "BrandSubtitle.TLabel",
            background="#13233F",
            foreground="#AFC3E4",
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "HeaderHint.TLabel",
            background="#13233F",
            foreground="#C6D4E9",
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TNotebook",
            background=background,
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background="#E7EDF6",
            foreground=muted,
            padding=(20, 11),
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", surface), ("active", "#DCE7F7")],
            foreground=[("selected", accent), ("active", foreground)],
        )
        style.configure("TLabelFrame", background=surface, bordercolor=border)
        style.configure(
            "TLabelframe.Label",
            background=surface,
            foreground=foreground,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TButton",
            background="#E8EEF7",
            foreground=foreground,
            padding=(13, 9),
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TButton",
            background=[("disabled", "#E5EAF1"), ("active", "#D9E4F3")],
            foreground=[("disabled", "#8B98AA")],
        )
        style.configure(
            "Accent.TButton",
            background=accent,
            foreground="#010101",
            padding=(15, 10),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", "#A9BDE8"), ("active", accent_hover)],
            foreground=[("disabled", "#F5F7FC")],
        )
        style.configure(
            "TEntry",
            fieldbackground=surface,
            foreground=foreground,
            insertcolor=foreground,
            padding=(8, 7),
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.configure(
            "TCombobox",
            fieldbackground=surface,
            background=surface,
            foreground=foreground,
            padding=(7, 6),
            arrowcolor=muted,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", surface)],
            foreground=[("readonly", foreground)],
            selectbackground=[("readonly", surface)],
            selectforeground=[("readonly", foreground)],
        )
        style.configure(
            "TCheckbutton",
            background=surface,
            foreground=foreground,
            padding=4,
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=accent,
            troughcolor="#E4EAF3",
            bordercolor="#E4EAF3",
            lightcolor=accent,
            darkcolor=accent,
            thickness=10,
        )
        style.configure(
            "Muted.TLabel",
            background=surface,
            foreground=muted,
        )
        style.configure(
            "Status.TLabel",
            background=surface,
            foreground=foreground,
            font=("Segoe UI", 10, "bold"),
            padding=(10, 8),
        )

    def _create_generate_tab(self):
        frame = self.generate_tab
        ttk.Label(
            frame,
            text="Generate a narrated, captioned video from your local clip library.",
            wraplength=680,
            style="Muted.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 14))

        ttk.Label(
            frame,
            text="Managed clips: data\\clips (imports are copied here)",
        ).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=4
        )
        self.ingest_button = ttk.Button(
            frame,
            text="Import local or synced Drive folder...",
            command=self._choose_clip_folder,
        )
        self.ingest_button.grid(row=1, column=2, sticky=tk.E, pady=4)
        self.drive_import_button = ttk.Button(
            frame,
            text="Import from cloud Drive...",
            command=self._choose_drive_folder,
        )
        self.drive_import_button.grid(row=1, column=3, sticky=tk.E, pady=4)

        ttk.Label(frame, text="Output orientation").grid(
            row=2, column=0, sticky=tk.W, pady=8
        )
        self.orientation = tk.StringVar(value="portrait")
        ttk.Combobox(
            frame,
            textvariable=self.orientation,
            values=("portrait", "landscape", "square"),
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky=tk.W, padx=(8, 20))

        ttk.Label(frame, text="Target video duration (seconds)").grid(
            row=3, column=0, sticky=tk.W, pady=8
        )
        self.story_duration = tk.StringVar(value="30")
        ttk.Entry(frame, textvariable=self.story_duration, width=10).grid(
            row=3, column=1, sticky=tk.W, padx=(8, 20)
        )
        ttk.Label(
            frame,
            text="The video targets this duration (20-90 seconds). The complete "
            "narration is kept; final length follows its measured audio and may "
            "vary by about 3-4 seconds. The topic guides the story and metadata; "
            "visual footage comes from your imported clip library.",
            wraplength=680,
        ).grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(0, 4))

        ttk.Label(frame, text="Story topic / category").grid(
            row=3, column=2, sticky=tk.W, pady=8
        )
        self.story_niche = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.story_niche, width=24).grid(
            row=3, column=3, sticky=tk.W, padx=(8, 0)
        )

        self.generate_button = ttk.Button(
            frame,
            text="Generate local video",
            command=self._start_generation,
            style="Accent.TButton",
        )
        self.generate_button.grid(
            row=5, column=0, columnspan=4, sticky=tk.EW, pady=(8, 10)
        )

        self.progress = ttk.Progressbar(
            frame, mode="determinate", maximum=100, value=0
        )
        self.progress.grid(row=6, column=0, columnspan=4, sticky=tk.EW, pady=4)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(
            frame,
            textvariable=self.status,
            wraplength=680,
            style="Status.TLabel",
        ).grid(
            row=7, column=0, columnspan=4, sticky=tk.W, pady=8
        )

        self.job_id_status = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.job_id_status).grid(
            row=8, column=0, columnspan=4, sticky=tk.W, pady=4
        )
        self.open_video_button = ttk.Button(
            frame, text="Open generated video", command=self._open_video, state=tk.DISABLED
        )
        self.open_video_button.grid(
            row=9, column=0, columnspan=2, sticky=tk.W, pady=(12, 0)
        )
        ttk.Label(
            frame,
            text="Videos are saved locally. Upload is always a separate manual action.",
            wraplength=680,
        ).grid(row=10, column=0, columnspan=4, sticky=tk.W, pady=(12, 0))

        recent_frame = ttk.LabelFrame(
            frame, text="Recently generated videos", padding=8
        )
        recent_frame.grid(
            row=11, column=0, columnspan=4, sticky=tk.NSEW, pady=(14, 0)
        )
        self.recent_outputs = tk.Listbox(
            recent_frame,
            height=6,
            exportselection=False,
            background="#F8FAFD",
            foreground="#17243A",
            selectbackground="#DCE8FC",
            selectforeground="#173A77",
            highlightthickness=1,
            highlightbackground="#DCE4EF",
            highlightcolor="#2563EB",
            relief=tk.FLAT,
            activestyle="none",
            font=("Segoe UI", 10),
        )
        self.recent_outputs.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True
        )
        recent_scrollbar = ttk.Scrollbar(
            recent_frame, orient=tk.VERTICAL, command=self.recent_outputs.yview
        )
        recent_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.recent_outputs.configure(yscrollcommand=recent_scrollbar.set)
        self.recent_outputs.bind(
            "<Double-Button-1>", lambda _event: self._open_selected_output()
        )
        self.recent_outputs.bind(
            "<<ListboxSelect>>", self._recent_output_selected
        )
        recent_buttons = ttk.Frame(frame)
        recent_buttons.grid(
            row=12, column=0, columnspan=4, sticky=tk.W, pady=(6, 0)
        )
        ttk.Button(
            recent_buttons, text="Refresh list", command=self._refresh_recent_outputs
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.open_selected_button = ttk.Button(
            recent_buttons,
            text="Open selected video",
            command=self._open_selected_output,
            state=tk.DISABLED,
        )
        self.open_selected_button.pack(side=tk.LEFT)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(2, weight=1)
        frame.rowconfigure(11, weight=1)

    def _create_settings_tab(self, frame):
        ttk.Label(frame, text="Story provider").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=8
        )
        self.story_provider = tk.StringVar()
        ttk.Combobox(
            frame,
            textvariable=self.story_provider,
            values=("mock", "openai", "anthropic", "gemini", "ollama"),
            state="readonly",
            width=47,
        ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=8)

        self.openai_entry = self._secret_entry(frame, "OpenAI API key:", 1)
        self.anthropic_entry = self._secret_entry(frame, "Anthropic API key:", 2)
        self.gemini_entry = self._secret_entry(frame, "Gemini API key:", 3)

        ttk.Label(frame, text="Ollama model").grid(
            row=4, column=0, sticky=tk.W, padx=5, pady=8
        )
        self.ollama_model = tk.StringVar(value="qwen2.5:3b")
        ttk.Entry(frame, textvariable=self.ollama_model, width=50).grid(
            row=4, column=1, sticky=tk.W, padx=5, pady=8
        )
        ttk.Label(frame, text="Ollama embedding model").grid(
            row=5, column=0, sticky=tk.W, padx=5, pady=8
        )
        self.ollama_embedding_model = tk.StringVar(value="nomic-embed-text")
        ttk.Entry(frame, textvariable=self.ollama_embedding_model, width=50).grid(
            row=5, column=1, sticky=tk.W, padx=5, pady=8
        )
        ttk.Label(frame, text="Ollama URL").grid(
            row=6, column=0, sticky=tk.W, padx=5, pady=8
        )
        self.ollama_base_url = tk.StringVar(value="http://127.0.0.1:11434")
        ttk.Entry(frame, textvariable=self.ollama_base_url, width=50).grid(
            row=6, column=1, sticky=tk.W, padx=5, pady=8
        )

        self.web_research_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable online web research (requires internet)",
            variable=self.web_research_enabled,
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, padx=5, pady=8)

        ttk.Label(frame, text="Narration TTS").grid(
            row=8, column=0, sticky=tk.W, padx=5, pady=8
        )
        self.tts_provider = tk.StringVar()
        ttk.Combobox(
            frame,
            textvariable=self.tts_provider,
            values=("offline", "openai", "mock"),
            state="readonly",
            width=47,
        ).grid(row=8, column=1, sticky=tk.W, padx=5, pady=8)

        self.research_docs_button = ttk.Button(
            frame,
            text="Index research documents folder...",
            command=self._choose_research_folder,
        )
        self.research_docs_button.grid(
            row=9, column=0, columnspan=2, sticky=tk.W, padx=5, pady=8
        )
        ttk.Label(
            frame,
            text="Offline mode is the default. Imported .txt, .md, or .pdf "
            "documents are searched locally; Ollama embeddings are optional "
            "and keyword retrieval is used if unavailable. Enable web research "
            "above only when internet access is available.",
            wraplength=520,
        ).grid(row=10, column=0, columnspan=2, sticky=tk.W, padx=5, pady=8)

        ttk.Label(
            frame,
            text="Offline uses Windows speech voices. Mock audio is silent. "
            "Ollama runs locally; cloud AI and YouTube upload require internet. "
            "Keys are masked and saved only to the local .env file.",
            wraplength=520,
        ).grid(row=11, column=0, columnspan=2, sticky=tk.W, padx=5, pady=12)
        ttk.Button(frame, text="Save local settings", command=self._save_settings).grid(
            row=12, column=0, columnspan=2, pady=12
        )

    @staticmethod
    def _secret_entry(frame, label, row):
        ttk.Label(frame, text=label).grid(
            row=row, column=0, sticky=tk.W, padx=5, pady=8
        )
        entry = ttk.Entry(frame, width=50, show="*")
        entry.grid(row=row, column=1, sticky=tk.W, padx=5, pady=8)
        return entry

    def _create_youtube_tab(self, frame):
        ttk.Label(
            frame,
            text="Connect one or more YouTube channels, then choose the account "
            "before uploading.",
            wraplength=650,
        ).pack(pady=(12, 8))
        ttk.Label(
            frame,
            text="Generated videos stay local until you choose Upload. Review the "
            "metadata and privacy setting before confirming.",
            wraplength=650,
        ).pack(pady=8)
        account_frame = ttk.LabelFrame(
            frame, text="YouTube accounts", padding=8
        )
        account_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(account_frame, text="Upload account").grid(
            row=0, column=0, sticky=tk.W, padx=4
        )
        self.youtube_account = tk.StringVar()
        self.youtube_account_combo = ttk.Combobox(
            account_frame,
            textvariable=self.youtube_account,
            state="readonly",
            width=32,
        )
        self.youtube_account_combo.grid(
            row=0, column=1, sticky=tk.W, padx=4, pady=4
        )
        self.youtube_account_combo.bind(
            "<<ComboboxSelected>>", self._select_youtube_account
        )
        ttk.Button(
            account_frame,
            text="Add / connect account...",
            command=self._connect_youtube,
        ).grid(row=0, column=2, padx=4)
        ttk.Button(
            account_frame,
            text="Remove selected account",
            command=self._remove_youtube_account,
        ).grid(row=0, column=3, padx=4)
        account_frame.columnconfigure(1, weight=1)
        self.yt_status_label = ttk.Label(frame, text="Status: Checking...")
        self.yt_status_label.pack(pady=10)
        self.connect_youtube_button = ttk.Button(
            frame, text="Connect YouTube channel", command=self._connect_youtube
        )
        self.connect_youtube_button.pack(pady=8)

        upload_frame = ttk.LabelFrame(
            frame, text="Manual video upload", padding=10
        )
        upload_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=12)

        self.upload_path_text = tk.StringVar(value="No video selected")
        ttk.Label(
            upload_frame,
            textvariable=self.upload_path_text,
            wraplength=600,
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=5)
        ttk.Button(
            upload_frame, text="Choose video file...", command=self._choose_upload_video
        ).grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Button(
            upload_frame,
            text="Use selected recent video",
            command=self._use_selected_recent_output,
        ).grid(row=1, column=1, sticky=tk.W, padx=8, pady=5)

        ttk.Label(upload_frame, text="Title").grid(
            row=2, column=0, sticky=tk.W, pady=(10, 4)
        )
        self.upload_title = tk.StringVar()
        ttk.Entry(upload_frame, textvariable=self.upload_title, width=75).grid(
            row=2, column=1, columnspan=2, sticky=tk.EW, pady=(10, 4)
        )

        ttk.Label(upload_frame, text="Description").grid(
            row=3, column=0, sticky=tk.NW, pady=4
        )
        self.upload_description = tk.Text(upload_frame, height=7, width=60)
        self.upload_description.grid(
            row=3, column=1, columnspan=2, sticky=tk.EW, pady=4
        )

        ttk.Label(upload_frame, text="Hashtags (comma-separated)").grid(
            row=4, column=0, sticky=tk.W, pady=4
        )
        self.upload_hashtags = tk.StringVar(value="")
        ttk.Entry(upload_frame, textvariable=self.upload_hashtags, width=40).grid(
            row=4, column=1, sticky=tk.EW, pady=4
        )
        self.generate_upload_metadata_button = ttk.Button(
            upload_frame,
            text="Generate title and description with selected AI",
            command=self._generate_upload_metadata,
        )
        self.generate_upload_metadata_button.grid(
            row=4, column=2, sticky=tk.E, padx=6, pady=4
        )

        ttk.Label(upload_frame, text="Visibility").grid(
            row=5, column=0, sticky=tk.W, pady=4
        )
        self.upload_visibility = tk.StringVar(value="private")
        ttk.Combobox(
            upload_frame,
            textvariable=self.upload_visibility,
            values=("private", "unlisted", "public"),
            state="readonly",
            width=18,
        ).grid(row=5, column=1, sticky=tk.W, pady=4)

        ttk.Label(upload_frame, text="Category").grid(
            row=6, column=0, sticky=tk.W, pady=4
        )
        self.upload_category = tk.StringVar(value="People & Blogs (22)")
        ttk.Combobox(
            upload_frame,
            textvariable=self.upload_category,
            values=(
                "Gaming (20)",
                "People & Blogs (22)",
                "Entertainment (24)",
                "Education (27)",
            ),
            state="readonly",
            width=24,
        ).grid(row=6, column=1, sticky=tk.W, pady=4)

        self.upload_kids = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            upload_frame,
            text="This video is made for kids",
            variable=self.upload_kids,
        ).grid(row=7, column=1, sticky=tk.W, pady=4)

        self.upload_button = ttk.Button(
            upload_frame,
            text="Upload selected video",
            command=self._start_youtube_upload,
            state=tk.DISABLED,
            style="Accent.TButton",
        )
        self.upload_button.grid(
            row=8, column=0, columnspan=3, sticky=tk.EW, pady=(12, 4)
        )
        ttk.Label(
            upload_frame,
            text="Review all details carefully. Metadata can be generated by the "
            "selected AI but must be checked before uploading.",
            wraplength=620,
        ).grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        upload_frame.columnconfigure(1, weight=1)

        self._update_youtube_status()

    def _load_settings(self):
        settings = load_settings()
        self.story_provider.set(settings.story_provider)
        self.tts_provider.set(settings.tts_provider)
        self.ollama_model.set(settings.ollama_model)
        self.ollama_embedding_model.set(settings.ollama_embedding_model)
        self.ollama_base_url.set(settings.ollama_base_url)
        self.web_research_enabled.set(settings.web_research_enabled)
        self.openai_entry.insert(0, settings.openai_api_key)
        self.anthropic_entry.insert(0, settings.anthropic_api_key)
        self.gemini_entry.insert(0, settings.gemini_api_key)

    def _save_settings(self):
        try:
            set_key(str(ENV_FILE), "STORY_PROVIDER", self.story_provider.get())
            set_key(str(ENV_FILE), "OPENAI_API_KEY", self.openai_entry.get())
            set_key(str(ENV_FILE), "ANTHROPIC_API_KEY", self.anthropic_entry.get())
            set_key(str(ENV_FILE), "GEMINI_API_KEY", self.gemini_entry.get())
            set_key(str(ENV_FILE), "OLLAMA_MODEL", self.ollama_model.get().strip())
            set_key(
                str(ENV_FILE),
                "OLLAMA_EMBEDDING_MODEL",
                self.ollama_embedding_model.get().strip(),
            )
            set_key(str(ENV_FILE), "OLLAMA_BASE_URL", self.ollama_base_url.get().strip())
            set_key(
                str(ENV_FILE),
                "WEB_RESEARCH_ENABLED",
                str(self.web_research_enabled.get()).lower(),
            )
            set_key(str(ENV_FILE), "TTS_PROVIDER", self.tts_provider.get())
            load_dotenv(ENV_FILE, override=True)
            messagebox.showinfo("Settings", "Local AI settings saved.")
        except OSError as error:
            messagebox.showerror("Settings error", f"Could not save settings: {error}")

    def _choose_clip_folder(self):
        directory = filedialog.askdirectory(
            title="Choose a folder containing video clips, including synced Drive folders"
        )
        if not directory:
            return
        self._begin_task("Importing clips...", indeterminate=True)

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
            messagebox.showinfo(
                "Cloud Drive is not configured",
                "Cloud Drive import requires the app publisher's Google Drive "
                "OAuth configuration and any Google verification it requires. "
                "You can still import a Google Drive for desktop synced folder "
                "using the local folder button.",
            )
            return

        folder_id = simpledialog.askstring(
            "Import from Google Drive",
            "Enter the Google Drive folder ID:",
            initialvalue=settings.google_drive_folder_id,
            parent=self,
        )
        if not folder_id or not folder_id.strip():
            return
        folder_id = folder_id.strip()

        try:
            set_key(str(ENV_FILE), "GOOGLE_DRIVE_FOLDER_ID", folder_id)
            load_dotenv(ENV_FILE, override=True)
        except OSError as error:
            messagebox.showerror("Could not save Drive folder", str(error))
            return

        self._begin_task(
            "Connecting to Google Drive and downloading clips...",
            indeterminate=True,
        )

        def import_drive_clips():
            try:
                count = self.runner.ingest_drive_folder(folder_id)
                self.events.put(("ingested", count))
            except Exception as error:
                self.events.put(("failed", str(error)))

        threading.Thread(target=import_drive_clips, daemon=True).start()

    def _choose_research_folder(self):
        directory = filedialog.askdirectory(
            title="Choose a folder containing research documents"
        )
        if not directory:
            return
        self._begin_task(
            "Indexing research documents with Ollama...", indeterminate=True
        )

        def index_documents():
            try:
                result = self.runner.ingest_research_directory(Path(directory))
                self.events.put(("research_documents_ingested", result))
            except Exception as error:
                self.events.put(("failed", str(error)))

        threading.Thread(target=index_documents, daemon=True).start()

    def _start_generation(self):
        try:
            story_duration = float(self.story_duration.get())
            if (
                not math.isfinite(story_duration)
                or story_duration < 20
                or story_duration > 90
            ):
                raise ValueError
            story_niche = self.story_niche.get().strip()
            if not story_niche:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid duration",
                "Video duration must be between 20 and 90 seconds. Enter a story topic.",
            )
            return

        if not messagebox.askyesno(
            "Generate local video",
            "This will run the selected story and TTS providers and save a video "
            "locally. It will not upload anything to YouTube. Continue?",
        ):
            return

        orientation = self.orientation.get()
        self.open_video_button.config(state=tk.DISABLED)
        self.output_path = None
        self.job_id_status.set("")
        self._begin_task("Starting local video job...")

        def generate():
            try:
                result = self.runner.run_job(
                    orientation=orientation,
                    story_duration=story_duration,
                    story_niche=story_niche,
                    on_job_created=lambda job_id: self.events.put(
                        ("job_created", job_id)
                    ),
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
        self.status.set(status)
        self.progress.stop()
        if indeterminate:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.configure(mode="determinate", maximum=100)
            self.progress["value"] = 0
        self.generate_button.config(state=tk.DISABLED)
        self.ingest_button.config(state=tk.DISABLED)
        self.drive_import_button.config(state=tk.DISABLED)
        if hasattr(self, "upload_button"):
            self.upload_button.config(state=tk.DISABLED)
        if hasattr(self, "generate_upload_metadata_button"):
            self.generate_upload_metadata_button.config(state=tk.DISABLED)
        if hasattr(self, "connect_youtube_button"):
            self.connect_youtube_button.config(state=tk.DISABLED)
        if hasattr(self, "research_docs_button"):
            self.research_docs_button.config(state=tk.DISABLED)
        if rotate_activity:
            self._schedule_activity_message()

    def _schedule_activity_message(self):
        if not self.active_task or not self.rotate_activity_messages:
            return
        delay_ms = random.randint(5000, 8000)
        self.activity_after_id = self.after(delay_ms, self._rotate_activity_message)

    def _rotate_activity_message(self):
        self.activity_after_id = None
        if not self.active_task:
            return
        self.activity_message = random.choice(
            tuple(message for message in ACTIVITY_MESSAGES if message != self.activity_message)
        )
        if self.current_stage:
            stage_text = self.current_stage.replace("_", " ").title()
            self.status.set(f"{self.activity_message}  |  {stage_text}")
        else:
            self.status.set(self.activity_message)
        self._schedule_activity_message()

    def _finish_task(self, completed=False):
        self.active_task = False
        self.active_job_id = None
        self.current_stage = None
        self.progress.stop()
        if completed:
            self.progress.configure(mode="determinate", maximum=100)
            self.progress["value"] = 100
        if self.activity_after_id is not None:
            self.after_cancel(self.activity_after_id)
            self.activity_after_id = None
        self.generate_button.config(state=tk.NORMAL)
        self.ingest_button.config(state=tk.NORMAL)
        self.drive_import_button.config(state=tk.NORMAL)
        if hasattr(self, "connect_youtube_button"):
            self.connect_youtube_button.config(state=tk.NORMAL)
        if hasattr(self, "research_docs_button"):
            self.research_docs_button.config(state=tk.NORMAL)
        self._update_upload_button_state()

    def _process_events(self):
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "job_created":
                self.active_job_id = payload
                self.job_id_status.set(f"Job: {payload}")
            elif event == "ingested":
                self._finish_task(completed=True)
                self.status.set(f"Imported {payload} new clip(s).")
            elif event == "research_documents_ingested":
                self._finish_task(completed=True)
                self.status.set(
                    f"Indexed {payload['files']} research document(s) "
                    f"into {payload['chunks']} searchable chunks."
                )
                if payload.get("warnings"):
                    self.status.set(
                        f"{self.status.get()} Ollama unload warning: "
                        f"{payload['warnings'][0]}"
                    )
            elif event == "completed":
                self._finish_task(completed=True)
                self.output_path = payload.get("output_path")
                self._refresh_recent_outputs()
                target_duration = payload.get("target_story_duration")
                narration_duration = payload.get("narration_duration")
                duration_summary = ""
                if narration_duration is not None and target_duration is not None:
                    duration_summary = (
                        f" Final video: {narration_duration:.1f}s "
                        f"(requested {target_duration:.0f}s; narration was not cut)."
                    )
                    if not payload.get("duration_within_tolerance", True):
                        duration_summary += (
                            " This is outside the requested +/-3.5s tolerance."
                        )
                self.status.set(
                    f"Completed: {payload['story_title']}.{duration_summary} "
                    f"Job artifacts: {payload['artifacts_dir']}"
                )
                warnings = payload.get("warnings", [])
                if warnings:
                    self.status.set(f"{self.status.get()} Warning: {warnings[0]}")
                if payload.get("research_warning"):
                    self.status.set(
                        f"{self.status.get()} {payload['research_warning']}"
                    )
                if self.output_path:
                    self.open_video_button.config(state=tk.NORMAL)
                else:
                    self.status.set(
                        "Story job completed; no video was produced. "
                        f"Artifacts: {payload['artifacts_dir']}"
                    )
            elif event == "failed":
                self._finish_task()
                self.status.set("Job failed.")
                messagebox.showerror("Automation failed", payload)
            elif event == "youtube_connected":
                self._finish_task()
                self._update_youtube_status()
                messagebox.showinfo(
                    "YouTube connected",
                    f"'{payload}' is connected and ready for uploads.",
                )
            elif event == "youtube_uploaded":
                self._finish_task(completed=True)
                self.youtube_id = payload["video_id"]
                self._update_youtube_status()
                messagebox.showinfo(
                    "Upload complete",
                    f"Video uploaded successfully:\n{payload['url']}",
                )
            elif event == "youtube_progress":
                percent = payload
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=100)
                self.progress["value"] = percent
                self.status.set(f"Uploading to YouTube... {percent}%")
            elif event == "youtube_upload_started":
                self.status.set("Uploading to YouTube... waiting for first chunk")
            elif event == "upload_metadata_generated":
                metadata = payload
                self.upload_title.set(metadata.title)
                self.upload_description.delete("1.0", tk.END)
                self.upload_description.insert("1.0", metadata.description)
                self.upload_hashtags.set(", ".join(metadata.hashtags))
                self._finish_task(completed=True)
                messagebox.showinfo(
                    "Metadata generated",
                    "Review the generated title, description, and hashtags before upload.",
                )
            elif event == "ollama_unload_warning":
                messagebox.showwarning("Ollama cleanup warning", payload)
            elif event == "youtube_failed":
                self._finish_task()
                self._update_youtube_status()
                messagebox.showerror("YouTube action failed", payload)

        if self.active_task and self.active_job_id:
            try:
                stage = self.runner.get_job_stage(self.active_job_id)
                if stage and stage != self.current_stage:
                    self.current_stage = stage
                    progress = JOB_PROGRESS.get(stage)
                    if progress is not None:
                        self.progress["value"] = max(
                            self.progress["value"], progress
                        )
                    self.status.set(
                        f"{self.activity_message}  |  "
                        f"{stage.replace('_', ' ').title()}"
                        + (
                            f" (provider timeout: "
                            f"{GEMINI_REQUEST_TIMEOUT_SECONDS}s)"
                            if stage == "GENERATING_STORY"
                            else ""
                        )
                    )
            except sqlite3.Error as error:
                self.status.set(f"Could not read job status: {error}")

        self.after(250, self._process_events)

    def _open_video(self):
        if not self.output_path:
            return
        self._open_output_path(Path(self.output_path))

    def _refresh_recent_outputs(self):
        self.recent_outputs.delete(0, tk.END)
        self.recent_output_paths = self.runner.get_recent_outputs()
        for path in self.recent_output_paths:
            job_id = path.parent.name
            modified = path.stat().st_mtime
            timestamp = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
            self.recent_outputs.insert(
                tk.END, f"{timestamp}  |  {job_id}  |  {path.name}"
            )
        self.open_selected_button.config(
            state=tk.NORMAL if self.recent_output_paths else tk.DISABLED
        )

    def _open_selected_output(self):
        selection = self.recent_outputs.curselection()
        if not selection:
            messagebox.showinfo(
                "Select a video", "Choose a generated video from the recent list."
            )
            return
        path = self.recent_output_paths[selection[0]]
        self._open_output_path(path)

    def _open_output_path(self, path: Path):
        path = Path(path)
        if not path.exists():
            messagebox.showerror("Video not found", f"Generated video is missing: {path}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                messagebox.showinfo("Generated video", str(path))
        except OSError as error:
            messagebox.showerror("Could not open video", str(error))

    def _update_youtube_status(self):
        self._refresh_youtube_accounts()
        settings = load_settings()
        account = self._selected_youtube_account()
        if account and self.youtube_accounts.token_path(
            account.account_id
        ).is_file():
            status = f"Connected account: {account.label}"
        elif settings.youtube_client_secret_file.exists():
            status = "OAuth credentials ready — connect required"
        else:
            status = (
                "YouTube sign-in is not configured in this app. Contact the app "
                "publisher; users should not need to create a Google Cloud project."
            )
        self.yt_status_label.config(text=f"Status: {status}")

    def _refresh_youtube_accounts(self):
        accounts = self.youtube_accounts.accounts()
        self._account_labels = {account.label: account for account in accounts}
        labels = list(self._account_labels)
        self.youtube_account_combo.configure(values=labels)
        active = self.youtube_accounts.active_account_id
        selected = next(
            (
                account.label
                for account in accounts
                if account.account_id == active
            ),
            labels[0] if labels else "",
        )
        self.youtube_account.set(selected)
        self._update_upload_button_state()

    def _selected_youtube_account(self):
        label = getattr(self, "youtube_account", None)
        if label is None:
            return None
        account = self._account_labels.get(label.get())
        if account is not None:
            return account
        active = self.youtube_accounts.active_account_id
        return next(
            (
                item
                for item in self.youtube_accounts.accounts()
                if item.account_id == active
            ),
            None,
        )

    def _select_youtube_account(self, _event=None):
        account = self._selected_youtube_account()
        if account:
            self.youtube_accounts.select_account(account.account_id)
        self._update_youtube_status()

    def _remove_youtube_account(self):
        account = self._selected_youtube_account()
        if not account:
            messagebox.showinfo("No account selected", "Connect an account first.")
            return
        if not messagebox.askyesno(
            "Remove YouTube account",
            f"Remove '{account.label}' and delete its locally saved sign-in token?",
        ):
            return
        try:
            self.youtube_accounts.remove_account(account.account_id)
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Account removal failed", str(error))
            return
        self._update_youtube_status()

    def _recent_output_selected(self, _event=None):
        selection = self.recent_outputs.curselection()
        if selection:
            self._select_upload_video(self.recent_output_paths[selection[0]])

    def _use_selected_recent_output(self):
        selection = self.recent_outputs.curselection()
        if not selection:
            messagebox.showinfo(
                "Select a video",
                "Select a video in the Generate Video tab first, or choose a video file.",
            )
            return
        self._select_upload_video(self.recent_output_paths[selection[0]])

    def _choose_upload_video(self):
        selected = filedialog.askopenfilename(
            title="Choose a video to upload",
            filetypes=(("MP4 video", "*.mp4"), ("All files", "*.*")),
        )
        if selected:
            self._select_upload_video(Path(selected))

    def _select_upload_video(self, path: Path):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() != ".mp4":
            messagebox.showerror(
                "Invalid video", "Choose an existing MP4 video file."
            )
            return
        self.upload_path = path
        self.upload_path_text.set(str(path))
        self.upload_title.set(path.stem[:100])
        self.upload_description.delete("1.0", tk.END)
        self.upload_hashtags.set("")
        self.upload_visibility.set("private")
        self.upload_category.set("People & Blogs (22)")
        self.upload_kids.set(False)

        metadata_path = path.parent / "metadata.json"
        if metadata_path.is_file():
            try:
                import json

                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                snippet = metadata.get("snippet", {})
                self.upload_title.set(snippet.get("title", path.stem)[:100])
                description = snippet.get("description", "")
                description_lines = description.splitlines()
                if (
                    description_lines
                    and description_lines[-1].split()
                    and all(
                    word.startswith("#")
                    for word in description_lines[-1].split()
                    )
                ):
                    self.upload_hashtags.set(
                        ", ".join(
                            word[1:]
                            for word in description_lines[-1].split()
                            if word.startswith("#")
                        )
                    )
                    description = "\n".join(description_lines[:-1]).rstrip()
                self.upload_description.insert("1.0", description)
                status = metadata.get("status", {})
                self.upload_visibility.set(
                    status.get("privacyStatus", "private")
                )
                self.upload_kids.set(
                    bool(status.get("selfDeclaredMadeForKids", False))
                )
                category_id = str(snippet.get("categoryId", "20"))
                category_labels = {
                    "20": "Gaming (20)",
                    "22": "People & Blogs (22)",
                    "24": "Entertainment (24)",
                    "27": "Education (27)",
                }
                self.upload_category.set(
                    category_labels.get(category_id, "People & Blogs (22)")
                )
            except (OSError, ValueError, AttributeError) as error:
                messagebox.showwarning(
                    "Metadata not loaded",
                    f"Could not read generated metadata; enter it manually. ({error})",
                )
        self._update_upload_button_state()

    def _generate_upload_metadata(self):
        if self.active_task:
            return
        if not self.upload_path or not self.upload_path.is_file():
            messagebox.showinfo("Select a video", "Choose an MP4 before generating metadata.")
            return
        path = self.upload_path
        fallback_description = self.upload_description.get("1.0", tk.END).strip()
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
                    import json

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
                        segments=[
                            StorySegment(
                                text=text,
                                start_time=0.0,
                                end_time=1.0,
                            )
                        ],
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
        if not hasattr(self, "upload_button"):
            return
        ready = (
            self.upload_path is not None
            and self.upload_path.is_file()
            and not self.active_task
        )
        if ready:
            settings = load_settings()
            account = self._selected_youtube_account()
            ready = (
                settings.youtube_client_secret_file.is_file()
                and account is not None
                and self.youtube_accounts.token_path(
                    account.account_id
                ).is_file()
            )
        self.upload_button.config(state=tk.NORMAL if ready else tk.DISABLED)
        if hasattr(self, "generate_upload_metadata_button"):
            self.generate_upload_metadata_button.config(
                state=tk.DISABLED if self.active_task else tk.NORMAL
            )

    def _start_youtube_upload(self):
        if not self.upload_path or not self.upload_path.is_file():
            messagebox.showerror("Video required", "Choose an existing MP4 first.")
            return
        title = self.upload_title.get().strip()
        description = self.upload_description.get("1.0", tk.END).strip()
        hashtags = [
            tag.strip().lstrip("#").replace(" ", "")
            for tag in self.upload_hashtags.get().split(",")
            if tag.strip().lstrip("#")
        ]
        visibility = self.upload_visibility.get()
        if not title or len(title) > 100:
            messagebox.showerror(
                "Invalid title", "Enter a title between 1 and 100 characters."
            )
            return
        if visibility not in ("private", "unlisted", "public"):
            messagebox.showerror("Invalid visibility", "Choose a valid visibility.")
            return
        category_id = self.upload_category.get().rsplit("(", 1)[-1].rstrip(")")
        if category_id not in {"20", "22", "24", "27"}:
            messagebox.showerror("Invalid category", "Choose a valid video category.")
            return

        metadata = YouTubeMetadata(
            title=title,
            description=description,
            hashtags=hashtags[:10],
            category=category_id,
            visibility=visibility,
            made_for_kids=self.upload_kids.get(),
        )
        if not messagebox.askyesno(
            "Confirm YouTube upload",
            f"Upload this video to YouTube?\n\n"
            f"Title: {metadata.title}\n"
            f"Visibility: {metadata.visibility}\n"
            f"File: {self.upload_path}\n\n"
            "This will upload the file and apply the selected visibility.",
        ):
            return

        upload_path = self.upload_path
        account = self._selected_youtube_account()
        if not account:
            messagebox.showerror(
                "YouTube account required",
                "Add and connect a YouTube account before uploading.",
            )
            return
        self._begin_task(
            "Authenticating and starting YouTube upload...",
            indeterminate=True,
            rotate_activity=False,
        )

        def upload():
            try:
                settings = load_settings()
                database = Database(Path("data/media.db"))
                uploader = YouTubeUploader(
                    db=database,
                    credentials_path=settings.youtube_client_secret_file,
                    token_path=self.youtube_accounts.token_path(
                        account.account_id
                    ),
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
                        progress_callback=lambda percent: self.events.put(
                            ("youtube_progress", percent)
                        ),
                    )
                self.events.put(
                    (
                        "youtube_uploaded",
                        {
                            "video_id": video_id,
                            "url": uploader.get_video_url(video_id),
                        },
                    )
                )
            except Exception as error:
                self.events.put(("youtube_failed", str(error)))

        threading.Thread(target=upload, daemon=True).start()

    def _connect_youtube(self):
        settings = load_settings()
        if not settings.youtube_client_secret_file.exists():
            messagebox.showerror(
                "OAuth credentials required",
                "YouTube sign-in is not configured in this app. Users should not "
                "need to create a Google Cloud project. Contact the app publisher.",
            )
            return
        if not messagebox.askyesno(
            "Connect YouTube",
            "A browser window will open. Choose the Google account for this YouTube "
            "channel and approve upload access. You do not need to create a Google "
            "Cloud project. Continue?",
        ):
            return
        label = simpledialog.askstring(
            "Name YouTube account",
            "Enter a name to identify this channel in the account list:",
            parent=self,
        )
        if label is None:
            return
        try:
            account = self.youtube_accounts.add_account(label)
        except ValueError as error:
            messagebox.showerror("Invalid account name", str(error))
            return
        self._refresh_youtube_accounts()

        def authenticate():
            try:
                database = Database(Path("data/media.db"))
                uploader = YouTubeUploader(
                    db=database,
                    credentials_path=settings.youtube_client_secret_file,
                    token_path=self.youtube_accounts.token_path(
                        account.account_id
                    ),
                )
                uploader.authenticate()
                self.events.put(("youtube_connected", account.label))
            except Exception as error:
                token_path = self.youtube_accounts.token_path(
                    account.account_id
                )
                if not token_path.exists():
                    try:
                        self.youtube_accounts.remove_account(account.account_id)
                    except (OSError, RuntimeError, ValueError):
                        pass
                self.events.put(("youtube_failed", str(error)))

        self._begin_task("Connecting YouTube channel...", indeterminate=True)
        threading.Thread(target=authenticate, daemon=True).start()


def main():
    from app.pyside_gui import main as pyside_main

    pyside_main()


if __name__ == "__main__":
    main()
