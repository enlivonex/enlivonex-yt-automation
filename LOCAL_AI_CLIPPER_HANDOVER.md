# Feature Handover: Local AI Video Clipper

## Goal

Add a new desktop workflow that takes long videos from a user-selected local
folder, uses AI running on the user's PC to find useful moments, and renders
reviewable short-form edits in the user's chosen format. The user can then
optionally upload an approved result through the existing YouTube account flow.

This is a new feature, not a replacement for the existing topic-led Shorts
generator or legacy Drive captioning pipeline.

## Intended user workflow

1. Select a folder of source videos and choose whether to include subfolders.
2. Select an input video and configure target duration, aspect ratio,
   resolution, captions, and permitted visual effects.
3. Analyze the video locally. Show progress and any unsupported or unreadable
   files with actionable errors.
4. Review AI-suggested moments with timestamps, transcript excerpts, and
   previews. Allow users to adjust start/end times and choose moments.
5. Render a draft locally. Support animated subtitles and optional zooms,
   emojis, and overlays from approved local assets.
6. Preview the finished result, edit its YouTube metadata, and explicitly
   confirm before uploading. Keep upload visibility private by default and
   preserve existing public-upload safeguards.

### Product decision to confirm before implementation

Should selected moments produce (a) one output file per moment, (b) one
compilation made from several moments, or (c) both? The original feature
request mentioned a single finished clip; confirm the preferred default with
the product owners before building the export UI.

## Scope and guardrails

- Process local files only in the first version. Do not download or scrape
  videos from YouTube or other services.
- Treat source videos as read-only. Never overwrite or delete originals.
- Ask users to confirm that they have the rights to edit and publish the
  selected videos and any included audio or visual material.
- Only use user-provided or appropriately licensed GIFs, meme assets, fonts,
  music, and overlays. Keep license/attribution information with bundled
  assets; do not scrape these assets from the web.
- Run transcription, moment analysis, and editing locally after the required
  models have been installed. Do not send source media to cloud AI providers.
- Upload only after the user previews and confirms the result. No automatic
  public uploads.
- Keep the feature separate from the current clip library: `data/clips` is
  used for background footage by the existing generator and should not become
  an untyped storage area for long-form source videos.

## Suggested technical approach

### Backend architecture

The backend is organized as a feature-local modular monolith with
ports-and-adapters boundaries:

- **Domain:** immutable source, transcript, and moment-candidate models.
- **Application:** a `LocalClipAnalyzer` service that coordinates inspection,
  transcription, and moment ranking without owning UI concerns.
- **Adapters:** an FFmpeg-backed local video catalog, a local
  Faster-Whisper transcriber, and an Ollama-backed moment ranker.
- **Interface boundary:** the desktop UI consumes the analysis result and
  later submits an explicit edit plan to a renderer. Provider-specific objects
  do not need to leak into the UI.

The first backend slice lives in `app/repurpose/`. Its public types are
re-exported from that package. The transcriber requires a preinstalled model
directory and enables local-files-only loading; it does not fetch models while
analyzing a video. `faster-whisper` is an optional dependency in
`requirements-ai.txt`.

The current analysis interface is:

```python
catalog.scan(folder_path, recursive=False) -> VideoScanResult
analyzer.analyze(
    video_path,
    min_duration=15,
    max_duration=60,
    max_candidates=8,
    language=None,
) -> ClipAnalysis
```

`VideoScanResult` separates inspected `sources` from per-file `failures`.
`ClipAnalysis` contains a `VideoSource`, ordered timestamped `transcript`
segments, and ranked `candidates` with start/end times, transcript, score, and
rationale. Candidate windows are sampled across the source and capped at 32 per
local-model ranking request to bound prompt size.

The rendering interface compiles an ordered set of user-selected source ranges
into one output file:

```python
plan = ClipEditPlan(
    source=analysis.source,
    ranges=(ClipRange(start_seconds=10, end_seconds=25),),
    transcript=analysis.transcript,
    settings=ClipOutputSettings(width=1080, height=1920),
)
rendered = LocalClipRenderer().render(plan, output_path)
```

The renderer rechecks the source hash, never writes over the source, uses
FFmpeg locally, optionally burns transcript captions, joins selected ranges,
and validates the output dimensions, duration, and audio stream. The UI can
consume these backend interfaces; the terminal workflow is implemented, while
the desktop UI integration remains future work.

The terminal workflow is available through `python -m app.repurpose.cli`:

```powershell
python -m app.repurpose.cli analyze "C:\Videos\source.mp4" `
  --whisper-model "C:\models\faster-whisper-small"

python -m app.repurpose.cli render "C:\Videos\source.analysis.json" `
  --candidate 1 --candidate 3 `
  --output "C:\Videos\source-clips.mp4"
```

Analysis uses Faster-Whisper and the configured local Ollama model; rendering
uses local FFmpeg. The output command joins the selected suggestions into one
MP4. Optional AI dependencies are installed with
`python -m pip install -r requirements-ai.txt`. Users must obtain the local
Whisper and Ollama model files before analysis; model acquisition is not done
implicitly while processing a video.

## Cost and offline expectations

The feature is designed to avoid per-video subscriptions and paid AI API calls:
transcription, moment ranking, and rendering run on the user's PC. Initial
installation/model downloads may require internet and disk space; ongoing
processing uses local compute, power, and storage. CPU-only PCs may be slower.
Third-party model, package, and FFmpeg licenses must be reviewed and
redistributed in accordance with their terms. YouTube upload remains subject
to Google's platform availability and quotas.

### New workflow boundary

Add a dedicated local-video-repurposing service/runner and a separate page or
mode in the PySide6 desktop interface. Avoid adding the full workflow to the
UI class or changing the semantics of the existing `JobOrchestrator`, which
generates topic-led narration over selected background clips.

Possible module names are suggestions, not requirements:

- Continue the existing `app/repurpose/` package for source inspection,
  transcription, moment selection, and edit-plan models.
- A `RepurposeJobRunner` (or equivalent) to coordinate analysis and rendering.
- A new PySide6 workflow that calls the runner and reports stage/progress
  events.

### Processing stages

1. **Import and inspect:** Enumerate supported video files, probe them with
   FFmpeg/FFprobe, compute a content hash for duplicate detection, and retain
   the original path. Do not copy large files unless the user explicitly
   chooses managed import.
2. **Transcribe:** Use a local speech-to-text model that returns timestamped
   text. Select the library/model after checking Windows support, CPU/GPU
   requirements, package size, license, and offline behavior.
3. **Find moments:** Combine transcript boundaries and scene changes to form
   candidate ranges. Use the configured local model to rank candidates for
   qualities such as a clear hook, coherent context, and a complete thought.
   Treat model results as suggestions, not final edits.
4. **Review and plan:** Display candidate timestamps and transcript context.
   Let the user edit ranges and create an explicit edit plan (EDL) containing
   source ranges, ordering, crop/zoom instructions, captions, overlays, and
   output settings.
5. **Render and validate:** Reuse the existing FFmpeg wrapper where practical.
   Render selected ranges, captions, optional effects, and audio without
   modifying the source. Validate output duration, dimensions, aspect ratio,
   and audio/video streams with FFprobe.
6. **Publish:** Reuse the existing metadata and YouTube upload/account
   components after user review and confirmation.

### Existing code to review and reuse

- `app/pyside_gui.py`: packaged PySide6 desktop UI and current user flows.
- `app/orchestration/runner.py`: current shared local-job entry point; use as
  a pattern, but do not conflate its job with repurposing.
- `app/orchestration/job.py`: existing topic-led Shorts job behavior.
- `app/clips/manager.py`: local video probing, hashing, and clip-library
  patterns. The new source-video collection should remain distinct.
- `app/utils/ffmpeg.py`, `app/composition/`, and `app/captions/`: existing
  FFmpeg, composition, and timed-caption code. Check compatibility before
  reusing the current narration-oriented composer.
- `app/ai/ollama.py` and `app/config/ai_providers.py`: current local model and
  provider configuration patterns.
- `app/database/db.py`: SQLite schema and initialization. Add a deliberate,
  backward-compatible migration if persistent repurposing state needs schema
  changes.
- `app/metadata/` and `app/youtube/`: metadata, account profiles, and upload.

The repository also contains an older Tkinter UI and CLI/Drive processing
pipeline. The packaged app starts through `run_enlivonex.py`; verify which UI
and workflow a change targets before editing documentation or shared code.

## MVP recommendation

Keep the first deliverable small:

- Select one local video from a chosen folder.
- Probe it and generate a local timestamped transcript.
- Produce ranked candidate moments within configurable minimum/maximum
  durations.
- Let the user adjust and select moments.
- Render a vertical draft with readable timed captions and a simple crop.
- Preview locally; leave upload as an explicit follow-up step or reuse the
  existing reviewed-upload flow if it can be connected cleanly.

Defer advanced animated styles, automatic zooms, emoji/GIF asset placement,
multi-video batch processing, and elaborate timeline editing until this
end-to-end path is stable.

## Acceptance criteria

- Existing video-generation and legacy CLI workflows continue to work.
- Source files are unchanged after import, analysis, failure, and rendering.
- Invalid files, unsupported formats, missing models, and insufficient disk
  space produce visible errors rather than success-shaped results.
- Every suggested range is within source duration and can be manually edited.
- A rendered output matches the selected format and passes media validation.
- The UI shows progress and supports cancellation without corrupting source
  files or leaving a job marked completed incorrectly.
- Analysis and rendering do not upload source media to a service.
- YouTube upload is optional, reviewed, and requires explicit confirmation.

## Tests and validation

- Add focused tests alongside the existing `tests/` modules for source
  discovery, duration/range validation, moment ranking with a fake local
  model, edit-plan serialization, and output-setting validation.
- Use small synthetic video/audio fixtures for FFmpeg integration tests.
- Mock transcription/LLM and YouTube upload in automated tests; tests must not
  require network access, model downloads, or real YouTube accounts.
- Run targeted tests for the changed modules, then the full test suite if
  practical. Update user documentation for the new workflow and its local
  model/system requirements.

## Collaboration workflow

1. Create a feature branch, for example `feature/local-ai-video-clipper`.
2. Start with the MVP and the open export decision above; keep changes scoped
   to this feature.
3. Open a pull request describing the user flow, architecture, tests, and any
   model/dependency/licensing decisions.
4. Do not commit `.env`, account tokens, local databases, source videos, or
   generated media.
