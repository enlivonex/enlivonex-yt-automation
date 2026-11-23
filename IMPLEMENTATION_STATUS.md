# Enlivonex YouTube Automation — Implementation Status

**Last Updated:** 2026-10-22
**Python Version:** 3.14.6
**Project:** C:\automation test

---

## Recent Fixes (2026-10-02)

Four major bugs were found and fixed during the recovery pass:

| Bug | File | Fix |
|-----|------|-----|
| `NameError: name 'platform' is not defined` | `app/cli_main.py` | Added `import platform` |
| `UnicodeEncodeError` on Windows CP1252 for emoji | `app/cli_main.py` | Replaced all emoji with ASCII-safe text |
| `Clip selection failed` with no context | `app/clips/selector.py`, `app/orchestration/job.py` | Overhauled selector to emit `ClipSelectionError` with clear diagnostic context showing why clips were rejected |
| `Audio merge failed: Invalid data` during full composition | `app/voiceover/provider.py`, `app/composition/composer.py` | Fixed `MockVoiceProvider` to generate genuine silent MP3s using FFmpeg instead of 0-byte invalid files. Also fixed FFmpeg composition `-filter_complex` syntax to correctly map input streams on Windows. |
| Output duration drifted from the requested target | `app/orchestration/job.py`, `app/composition/` | The user selects one target (20-90 seconds); prompts aim within about 3.5 seconds, full narration/captions are preserved, and rendering follows measured narration duration. |

---

## Module Status

| Module | Status | Notes |
|--------|--------|-------|
| CLI (`cli_main.py`) | **WORKING** | All commands verified on Windows |
| CLI (`cli.py`) | PARTIALLY WORKING | Older Drive-based CLI; has non-ASCII chars in logger strings, but not the active entry point |
| Database (`database/`) | **WORKING** | SQLite, all tables, idempotent |
| Clip Manager (`clips/manager.py`) | **WORKING** | Ingest, dedup by hash, FFprobe metadata |
| Clip Selectors (`clips/selector.py`) | **WORKING** | Orientation and duration rank available clips; least-used clips rank first within preference tiers |
| Story Provider (`story/provider.py`) | **WORKING** | Template/mock is the default. Ollama, OpenAI, Anthropic, and Gemini are selectable; prompts prioritize the user-selected topic and retrieved research, treat footage as unrelated b-roll, and avoid visual claims. |
| Voiceover Provider (`voiceover/`) | **WORKING** | Offline Windows TTS is the default and produces spoken narration; OpenAI TTS is optional. Mock remains available for tests; it intentionally produces silence. |
| Timed Captions (`captions/`) | **WORKING** | TimedCaptionGenerator, ASS/SRT/VTT export |
| Video Composition (`composition/composer.py`, `composition/duration.py`) | **WORKING** | Full narration and captions are preserved; rendering follows measured narration duration, looping video-only footage when needed. Portrait, landscape, or square output dimensions follow the GUI selection; final duration and audio stream are verified. |
| Metadata Generator (`metadata/`) | **WORKING** | Template-based or selected local/cloud AI title, description, and hashtag generation |
| Job Orchestrator (`orchestration/job.py`) | **WORKING** | Full stage pipeline, stage tracking in DB |
| FFmpeg Wrapper (`utils/ffmpeg.py`) | **WORKING** | Available: ffmpeg 9.0.2-essentials |
| Dashboard (`dashboard/app.py`) | **WORKING** | Flask at http://127.0.0.1:5000, API endpoints verified |
| YouTube Upload (`youtube/uploader.py`) | **PARTIAL** | Manual GUI OAuth and resumable upload flow is implemented; requires the user's OAuth Desktop client and has not yet been live-tested |
| YouTube Analytics (`youtube/analytics.py`) | **MOCKED** | MockYouTubeAnalytics works; real credentials missing |
| Google Drive (`drive/client.py`) | **EXTERNALLY BLOCKED** | No OAuth credentials configured |

---

## Duration and Synchronization Milestone (2026-10-05)

**WORKING**

- Orientation and duration selection are preference tiers; they do not exclude
  the only available clip. Least-used ranking is preserved within each tier.
- A narration longer than the selected clip uses further distinct, available
  clips ranked by the configured selector. If the library is exhausted, the
  last available video clip is looped to cover the measured narration.
- `DurationManager` compares probed source-video duration with probed merged
  narration duration. It requests a video-only loop when the source is too
  short; otherwise the render is bounded to the narration duration, trimming
  longer sources and using approximately equal sources directly.
- Narration is mapped once and is never looped. For multiple source clips,
  each video stream is normalized and concatenated before the existing caption
  filter; looping, when needed, applies only to the last video input.
- FFprobe checks the final container duration against the merged narration
  (0.5-second tolerance) and checks that an audio stream is present. A failed
  check fails the job instead of reporting success.
- Full local job `JOB-20261005-FF7A08` completed against the registered 427.06s
  landscape source. It produced a playable 1080x1920 `output.mp4` of 13.267s;
  the combined narration segments measured 13.200s (0.067s difference), an
  audio stream was present, captions were generated, and full FFmpeg decoding
  succeeded. The current mock audio is valid but silent.

**MOCKED**

- `MockVoiceProvider` still produces silent audio for tests and explicit mock
  mode; production default is now offline spoken TTS.

## Multi-Clip Narration and Spoken TTS (2026-10-21)

**WORKING**

- Story generation targets the user-selected duration regardless of the first
  source clip's duration. Once TTS is generated, measured segment duration
  determines how many distinct available clips are needed.
- The first selected clip remains the story context. Additional clips are
  chosen from the available library in preference/least-used order and
  concatenated in the final render. If there are not enough clips, composition
  loops only the final video clip; narration/audio remains single-pass.
- `TTS_PROVIDER=offline` is the default. It uses local Windows speech voices
  through `pyttsx3`, generates spoken WAV audio, converts it to MP3 with FFmpeg,
  and probes actual durations for captions and synchronization. The OpenAI TTS
  option remains available; mock mode remains intentionally silent.
- The Windows SAPI provider queues all story segments and runs the speech
  event loop once per story. Running `pyttsx3.runAndWait()` after every saved
  segment could leave later segments stuck in `GENERATING_VOICE`.
- The GUI exposes offline, OpenAI, and mock TTS selection. It does not use or
  request ElevenLabs credentials.
- Local end-to-end media check used Windows offline speech and two generated
  source clips: FFprobe measured 3.079s of speech and 3.067s of output video;
  the MP3 had a measured mean volume of -19.7 dB, output decoded successfully,
  and captions plus the audio stream were present. This was a synthetic local
  check, not a run against the user's clip library or Gemini API.

## User-Selected Niche and Duration (2026-10-22)

- The GUI and CLI accept a required niche and a single requested duration from
  20 to 90 seconds instead of selecting a random duration from a range.
- Story-provider prompts now identify the selected niche as the subject of the
  hook, title, and narration; the template provider also uses that niche.
- Story text, generated audio, and captions are no longer truncated to meet a
  target. The render uses measured narration duration and reports when the
  measured audio differs from the requested duration by more than 3.5 seconds.
- Visual scenes still come from clips imported by the user. To keep visuals
  aligned with a niche, the clip library must contain footage for that niche;
  this system does not generate or analyze video imagery.
- Reproduced the Windows multi-segment SAPI hang and confirmed that the event
  loop must be run once for queued segments, not once per segment. After the
  fix, two actual SAPI-generated MP3 segments measured -22.0 dB and -22.9 dB;
  a two-source video with those narration segments and captions decoded with
  audio/video durations within 0.058s.
- Story-only CLI dry run `JOB-20261021-3A0944` completed successfully. As
  designed, `--dry-run` does not exercise TTS or compose a video.

## Topic-Driven Research and Local RAG (2026-10-22)

- The user-entered topic is passed to research and story generation; source
  clip names and gameplay footage are treated as unrelated b-roll, not topic
  suggestions.
- Web search results are indexed as snippets in SQLite. User-selected folders
  can also be indexed from `.txt`, `.md`, and `.pdf` files. Retrieval uses local
  Ollama embeddings and includes matching evidence with source citations in
  generated metadata descriptions.
- Web evidence is scoped to the current topic, and retrieval excludes chunks
  created with a different embedding model. Imported local documents remain
  available across topics.
- Web research is now opt-in and disabled by default. With it disabled, story
  generation and local-document retrieval make no internet requests. Missing
  Ollama embedding models fall back to offline keyword retrieval, and a topic
  with no imported evidence can still proceed without citations.
- Setup: install optional dependencies with
  `.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt`; ensure
  the selected local Ollama story model is installed. Semantic embeddings are
  optional; web research uses search-result snippets and requires enabling the
  online research checkbox and internet access.
- Cloud providers, web search, YouTube authorization/upload, and fresh online
  citations require internet. The local Ollama + offline TTS + indexed-document
  generation path is designed to run without an external network.

## Generated Video Retention

- After a successful generation, the app retains the 10 newest completed job
  folders that contain a non-empty `output.mp4` and removes older matching
  generated job folders.
- Failed and in-progress jobs, source clips, and database history are
  preserved. Database job/artifact records are intentionally not deleted.

## Product Branding, Account Profiles, and Local Security

- User-facing app, CLI, dashboard, workflow, and project-document titles use
  the Enlivonex YouTube Automation name.
- The desktop uploader supports multiple named YouTube profiles. Each profile
  has a separate token; uploads require a selected connected profile. The
  OAuth scope remains limited to `youtube.upload`.
- Windows OAuth tokens are encrypted with DPAPI for the current Windows user.
  Existing single-account token files are migrated into a default profile and
  removed only after the protected copy is written. Other platforms use
  restrictive token-file permissions where supported.
- Dashboard CORS is disabled; the dashboard binds to localhost by default.
  Do not bind it to a public or untrusted network without adding authentication.
- This is targeted local credential and browser-origin hardening, not a
  comprehensive security audit or a guarantee that a PC or application cannot
  be compromised. AI API keys in `.env` remain plaintext and must be kept
  private; public distribution still needs a broader security review.

**EXTERNALLY BLOCKED**

- Offline TTS needs a working Windows SAPI speech voice. OpenAI TTS needs
  `OPENAI_API_KEY`. No API request or YouTube upload is required for offline
  narration.

## PySide6 Desktop UI (2026-10-23)

**WORKING**

- The PySide6 app can import a local clip directory and launch the existing
  configured story → TTS → captions → composition job without CLI arguments.
- The Create video, AI settings, and YouTube studio pages provide a modern
  navigable interface. Generation runs in a worker thread; the window remains
  responsive and shows the current persisted job stage.
- The CLI and GUI share `LocalJobRunner` and continue using the established
  provider interfaces, clip selectors, `JobOrchestrator`, database, and
  composer.
- AI provider and TTS provider configuration can be changed in the GUI and
  saved to the local `.env` file. Keys are masked in the UI.
- Launch from the project root with `python -m app.gui_main`. The previous
  Tkinter `DashboardApp` remains available as a fallback; the default launcher
  starts PySide6.
- The Create video page lists recent non-empty `output.mp4` files under
  `data/jobs/JOB-*/`, supports refreshing the list, and opens a selected output
  with the system's default video player. New completed jobs refresh the list.
- The GUI no longer exposes a source-clip duration filter. Each run chooses a
  target inside the selected 20–90 second range and hard-caps generated story
  word count, merged narration audio, captions, and final output to that target.
- The GUI requires an explicit portrait, landscape, or square output selection.
  Source clips are normalized into those output dimensions regardless of their
  source orientation; no random orientation is selected.
- Story generation receives the user-entered topic, a varied narrative angle,
  an approximate spoken-word target, retrieved research, and the eight most
  recent saved titles/hooks to discourage repetitive scripts. Background clips
  are unrelated b-roll and are not visually analyzed.
- Gemini story requests have a 90-second HTTP timeout with SDK retries
  disabled. A timeout fails the job with a safe, explicit message instead of
  leaving the GUI waiting indefinitely at `GENERATING_STORY`.

**NOT IN SCOPE / FUTURE**

- Video generation never uploads automatically. A separate manual upload action
  is available in the YouTube studio page after selecting an existing MP4.
- AI-generated metadata and local-model metadata providers are not wired into
  the manual upload flow; the user reviews/enters title, description, hashtags,
  category, audience declaration, and visibility before confirming upload.
- Automated view/like monitoring, comment replies, and AI engagement are not
  implemented. They should be considered in a separate phase with explicit
  YouTube API and moderation safeguards.

**MANUAL YOUTUBE UPLOAD**

- Configure `YOUTUBE_CLIENT_SECRET_FILE` to point to an OAuth Desktop client
  JSON downloaded from Google Cloud Console (default:
  `data/youtube_client_secret.json`). Enable the YouTube Data API for that
  Google Cloud project. The OAuth token is stored at `YOUTUBE_TOKEN_FILE`
  (default: `data/youtube_token.json`).
- In the YouTube studio page, connect the channel through the browser OAuth flow.
  Then choose an MP4 (or choose one selected in the recent outputs list), review
  or edit metadata and visibility, and click **Upload selected video**. A
  confirmation dialog appears before any video upload.
- Leaving the upload action untouched keeps generated outputs local. Upload
  runs off the Qt UI thread and uses resumable chunks. The returned
  YouTube URL is shown after successful upload and the video record is saved to
  `data/media.db`.
- Live authentication/upload requires the user's OAuth client file, channel
  authorization, network access, and YouTube API quota. Automated tests use the
  existing mock uploader and do not authorize or upload to YouTube.

## AI Story and TTS Provider Milestone (2026-10-05)

**IMPLEMENTED; REAL API EXECUTION REQUIRES USER CREDENTIALS**

- The existing story-provider interface is wired to selectable OpenAI,
  Anthropic, or Gemini story generation. Prompts request original narration for the
  requested duration and explicitly disallow assertions about visuals because
  the application supplies clip metadata only.
- The existing voice-provider interface supports offline Windows TTS and
  optional OpenAI TTS. Generated segment files are measured using FFprobe, with sequential
  start/end times based on measured duration. This preserves the existing
  narration-driven composition and caption pipeline.
- Mock/template story generation remains the default. Offline TTS is the
  narration default; silent mock TTS remains available explicitly.
  `run-job` selects providers from the dotenv-backed Settings configuration.
- Explicit OpenAI TTS selection without its API key stops before creating a
  job with a clear configuration message.
  Configuration status contains only `configured` / `not configured`; keys
  are not displayed.
- Optional SDK dependencies are in `requirements-ai.txt`; install them with
  `pip install -r requirements-ai.txt` to enable real SDK use. Gemini uses
  Google's official `google-genai` SDK (`google-genai>=1.0,<3.0`); the project
  interpreter tested here is Python 3.14.6 and the installed SDK is 2.28.0.

Configuration:

| Variable | Purpose |
|----------|---------|
| `STORY_PROVIDER` | `mock` (default), `openai`, `anthropic`, `gemini`, or `ollama` |
| `TTS_PROVIDER` | `offline` (default), `openai`, or `mock` |
| `OPENAI_API_KEY` | OpenAI story and/or TTS credentials |
| `ANTHROPIC_API_KEY` | Anthropic story credentials |
| `GEMINI_API_KEY` | Gemini story credentials |
| `OPENAI_STORY_MODEL`, `ANTHROPIC_STORY_MODEL` | Story model selection |
| `GEMINI_STORY_MODEL` | Gemini story model (default `gemini-3.8-flash`) |
| `OLLAMA_MODEL`, `OLLAMA_BASE_URL` | Local Ollama model and service URL |
| `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE` | OpenAI speech model and voice |

## Local Ollama and Upload Reliability (2026-10-22)

**IMPLEMENTED**

- `ollama` is now an available story provider and can generate video metadata
  locally using the selected Ollama model. `qwen2.5:3b` is the default local
  model; the app uses Ollama's local HTTP API, starts the local service when it
  is not responding, and checks that the configured model is installed.
- Ollama requests keep the model available for the job; after the job ends the
  runner explicitly asks Ollama to unload it (`keep_alive: 0`) to release RAM
  and VRAM. Standalone AI metadata generation also unloads the model afterward.
- YouTube resumable uploads now configure `httplib2` not to auto-follow HTTP
  308, which is YouTube's normal resumable-upload status rather than a redirect.
- The GUI exposes the Ollama model and local service URL. Select `ollama` under
  **AI Settings** and save. Ollama does not require a cloud AI key. Cloud story
  providers remain selectable; generated metadata uses the selected cloud
  provider or local model. Review all generated metadata before upload.
- Manual YouTube uploads now report chunk progress. Requests have a network
  timeout, retry once on retryable chunk errors, and fail with an explicit
  no-progress message rather than looping indefinitely. The GUI keeps the real
  upload status visible instead of replacing it with rotating activity text.
- Ollama metadata generation tolerates missing title, description, or hashtag
  fields by filling them from the saved story and logs a warning. It still
  rejects fields with invalid types rather than failing with an unhandled
  `KeyError`.
- Unit tests cover local-provider parsing, provider selection, upload progress,
  and a stalled upload. No live YouTube upload is performed by automated tests.

**LOCAL SETUP**

1. Start Ollama and confirm `qwen2.5:3b` appears in `ollama list`. If missing,
   run `ollama pull qwen2.5:3b` once.
2. In the app's **AI Settings**, choose `ollama`, keep model
   `qwen2.5:3b` and URL `http://127.0.0.1:11434`, then save settings.
3. Use the YouTube Setup metadata button to draft title/description/hashtags,
   then review the fields. Generation and metadata drafting are local when
   Ollama is selected. YouTube upload itself still uses YouTube OAuth and API.

**LIMITATION**

The implementation can bound a stalled client request and show progress, but
cannot guarantee YouTube accepts an upload. OAuth/token problems, API quota,
network outages, project restrictions, and YouTube processing remain external
dependencies. If a request fails after YouTube has received the file, check
YouTube Studio before retrying because the remote upload may have succeeded
before the local database record was written.

Offline TTS uses `pyttsx3` and Windows speech voices; it requires no API
credential. `requirements.txt` includes the package. The application does
not select ElevenLabs.

Gemini story generation uses the official Google Gen AI SDK Interactions API
(`client.interactions.create`) and JSON Schema structured output. The default
model is `gemini-3.8-flash`; set `GEMINI_STORY_MODEL` in `.env` to override it.
Automated tests mock the Interactions API and do not make live requests.

### Gemini story smoke test (manual; no upload)

1. Install the optional Gemini SDK if it is not already installed:
   `.\.venv\Scripts\python.exe -m pip install "google-genai>=1.0,<3.0"`
2. In the untracked local `.env`, set `STORY_PROVIDER=gemini`,
   `GEMINI_API_KEY=<your key>`, and `TTS_PROVIDER=offline` for spoken local
   narration on a full run.
3. From the project root, run:
   `.\.venv\Scripts\python.exe -m app.cli_main run-job --dry-run`

`--dry-run` generates and stores the story, then skips TTS, composition, and
YouTube. No key should be pasted into the terminal command or source code.

## Full Pipeline Run Status

The full job execution (without `--dry-run`) is now **WORKING**. 

```
CLI run-job --orientation portrait --max-duration 60
  |
  +-- Job Creation          WORKING    JOB-XXXXXXXX-XXXXXX
  |
  +-- Clip Selection        WORKING    Ranks preferences, uses distinct clips to cover narration, then loops the last available clip if needed
  |
  +-- Story Generation      WORKING    Configurable mock/template, Ollama, OpenAI, Anthropic, or Gemini provider; target up to 60 seconds independent of source clip length
  |
  +-- Voiceover             WORKING    Offline Windows TTS by default; optional OpenAI TTS; mock is silent
  |
  +-- Captions              WORKING    ASS subtitles generated
  |
  +-- Composition           WORKING    FFmpeg adapts video duration to narration, merges audio, burns captions, and verifies output
  |
  +-- Metadata              WORKING    Generated
  |
  +-- Job Completion        WORKING    Status: COMPLETED, artifacts saved to data/jobs/
```

- `output.mp4` was successfully generated in the job directory.

---

## Test Results

```
Focused TTS, configuration, clip orchestration, composition, and database tests:
52 passed.
```

The prior 134-test baseline passed before this milestone. Existing audio merge,
caption, and duration verification tests remain in place.

---

## Commands Verified

| Command | Result |
|---------|--------|
| `python -m app.cli_main verify` | 6/6 PASS |
| `python -m app.cli_main status` | WORKING — no UnicodeEncodeError |
| `python -m app.cli_main list-jobs` | WORKING |
| `python -m app.cli_main run-job --dry-run` | WORKING — job completes |
| `python -m app.cli_main run-job` | WORKING — full composition completes |
| `python -m app.cli_main dashboard --port 5000` | WORKING — HTTP 200 |

---

## External Blockers

| Blocker | Impact | Resolution |
|---------|--------|------------|
| No YouTube OAuth credentials (`client_secrets.json`) | Cannot upload or fetch analytics from real YouTube | Configure credentials per YouTube Data API v3 docs |
| No Google Drive OAuth credentials | Cannot use Drive-based scan/download CLI (`cli.py`) | Configure credentials per Google Drive API docs |
| No story-generation API credentials | Cloud story providers are unavailable; template and local Ollama generation do not require a cloud AI key | Select `ollama` with the local Ollama service running, or configure a cloud provider and key |

---

## Recommended Next Steps

1. **Configure YouTube OAuth** to unblock real upload / analytics
2. **Configure `STORY_PROVIDER` and `TTS_PROVIDER`**; local Ollama story/metadata generation and offline spoken TTS require no cloud API key, while cloud providers and OpenAI TTS require credentials
3. **Consider Gemini API** as an alternative AI provider (already in your environment)
