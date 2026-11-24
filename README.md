# Enlivonex YouTube Automation

## YouTube account profiles and local security

The desktop app supports multiple named YouTube account profiles. Each profile uses its own upload-only OAuth token, and uploads require selecting a connected account and confirming the upload. On Windows, saved YouTube tokens are
protected with DPAPI for the current Windows user. On other operating systems,token files use restrictive filesystem permissions where supported.

The OAuth client belongs to the application publisher, not each user. Once the publisher has configured and distributed the app's OAuth client, users select their own Google account in the sign-in browser and grant upload permission;
they should not create a Google Cloud project or supply client credentials.
The publisher must complete any Google consent-screen and API compliance steps required for public use. Until that is done, Google may restrict sign-in to accounts listed as test users.

This protection does not make the app or the computer impossible to compromise.
Keep Windows updated, protect your account and device, do not share token files, and do not expose the local dashboard to untrusted networks. AI provider keys saved in `.env` are not encrypted by this feature.

A local-first automation pipeline for processing and uploading media content from Google Drive to YouTube. Built for testing and development with safety-first design principles.

## Local AI video clipper

The desktop app includes a dedicated local clipper page for selecting a video,
reviewing and trimming AI-suggested moments, then rendering a portrait, square,
or landscape draft. Speech can be auto-detected or assigned a known language.
Captions, optional creator credits, brightness, contrast,
saturation, sharpness, and grain are configurable before rendering. Processing
uses the local Faster-Whisper, Ollama, and FFmpeg pipeline; source videos remain
unchanged. The existing topic-led video generator and YouTube review safeguards
remain separate and unchanged. See
[LOCAL_AI_CLIPPER_HANDOVER.md](./LOCAL_AI_CLIPPER_HANDOVER.md) for the
teammate-facing frontend and Windows build guide.

## Importing source footage

The desktop app copies imported video files into the managed `data/clips` library before registering them. It never moves or deletes the originals, and duplicate content is not copied again. Name collisions are stored under a hash-suffixed filename rather than overwriting a different clip.

Use **Import local or synced Drive folder** to choose a normal folder or a Google Drive for desktop synced folder. Use **Import from cloud Drive** to download directly from a Google Drive folder through the Drive API. Direct cloud access requires the app publisher's Google OAuth client configuration (`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`) and may require Google consent screen verification for the Drive scope. End users should not create their own
Google Cloud project; the publisher must configure and verify the app. 

In the Windows testing ZIP, OAuth configuration is intentionally not bundled, so cloud import is unavailable until the publisher configures it locally. Synced Drive folders can still be imported without Drive API credentials.

## Overview

This project automates the following workflow:

1. **Discover** - Scan a Google Drive folder for new media files
2. **Download** - Fetch files locally
3. **Validate** - Check format, size, and readability
4. **Caption** - Generate funny/dark humor captions
5. **Render** - Overlay captions on images/videos using clean typography
6. **Track** - Record processing state in SQLite
7. **Upload** - (Optional) Upload videos to YouTube with privacy controls

**Key Features:**
- ✅ Local-first development with DRY_RUN mode
- ✅ Duplicate prevention (idempotent processing)
- ✅ Public upload protection (requires explicit confirmation)
- ✅ Clean caption rendering with FFmpeg and Pillow
- ✅ Template-based caption generation (no external AI required)
- ✅ Comprehensive CLI interface
- ✅ Full test coverage

## Architecture

```
app/
├── config/         # Environment-based configuration
├── drive/          # Google Drive API integration
├── media/          # Image/video processing and validation
├── captions/       # Caption generation strategies
├── youtube/        # YouTube API integration
├── database/       # SQLite state management
├── pipeline/       # Main orchestration logic
└── utils/          # Logging and utilities

tests/              # Pytest test suite
data/               # Local data storage
├── clips/          # Managed source video library (imports are copied here)
├── input/          # Downloaded media files for the legacy pipeline
├── output/         # Processed media with captions
└── temp/           # Temporary processing files

logs/               # Application logs
```

## Prerequisites

### System Requirements

- **Python 3.11+**
- **FFmpeg** (for video processing)
- **Git** (for cloning)

### Google Cloud Setup

1. Create a Google Cloud Project
2. Enable **Google Drive API**
3. Enable **YouTube Data API v3**
4. Create OAuth 2.0 credentials (Desktop application type)
5. Download client credentials JSON

### Installation

#### Windows testing package

To build a distributable Windows desktop folder and ZIP, run
`powershell -ExecutionPolicy Bypass -File scripts/package_windows.ps1` on a
Windows build machine with Python 3.11 or newer. The result is
`dist/Enlivonex-Windows.zip`; testers extract it and launch `Enlivonex.exe`.
The build bundles the app and Python dependencies. FFmpeg and Ollama are
detected and can be installed from **AI settings → Install missing
components**. Model downloads are large and happen only after the tester starts
that action.

The package excludes `.env`, AI provider API keys, YouTube account tokens,
local databases, logs, research documents, and generated media. If the
distributed app needs Google sign-in, pass the publisher's Google Desktop app
OAuth client JSON to include it in the ZIP:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/package_windows.ps1 `
  -Python .\.venv\Scripts\python.exe `
  -OAuthClientSecretPath "$env:USERPROFILE\Downloads\client_secret_your-client-id.json"
```

The script requires the OAuth JSON to remain outside the project directory and
copies it only into the package at `data\youtube_client_secret.json`. Google
Desktop apps are public OAuth clients: anyone who receives the ZIP can extract
the client ID and `client_secret`, which must not be treated as confidential
or used as a server credential. This client configuration may be distributed
with the desktop app. Never include a tester's account token, `.env`, or AI
provider API keys. Omit `-OAuthClientSecretPath` to build without Google
sign-in configuration.

Each tester's settings and working data, including the managed `data/clips`
library, are created beside the executable, so the app folder must be writable
by that Windows user. Local and Google Drive for desktop synced-folder imports
work without cloud API credentials. Direct cloud Drive imports require the
publisher's separately configured OAuth client and any Google verification
required for the Drive scope. The
Python source is not shipped as loose files, but a packaged Python executable
is not tamper-proof or impossible to reverse engineer. The included Desktop
OAuth configuration is extractable and must not be treated as a protected
secret; never embed server credentials or private signing keys. Follow Google's
current native-app OAuth requirements, including PKCE, and complete any
consent-screen verification and API compliance steps required for public
distribution.

The local RAG index remains in the app's SQLite database. Its embedding model
is independently selectable from the Ollama models installed on the tester's
PC; choose an embedding-capable model for document indexing and retrieval.

#### 1. Clone and Setup

```bash
git clone <repository-url>
cd automation-test

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### 2. Install FFmpeg

**Windows:**
```bash
# Using Chocolatey
choco install ffmpeg

# Or download from https://ffmpeg.org/download.html
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install ffmpeg
```

Verify installation:
```bash
ffmpeg -version
```

#### 3. Configure Environment

```bash
# Copy example configuration
cp .env.example .env

# Edit .env with your settings
notepad .env  # Windows
nano .env     # Linux/macOS
```

**Required Configuration:**

```env
# Google Drive
GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret_here
GOOGLE_DRIVE_FOLDER_ID=your_google_drive_folder_id

# YouTube (create client_secret JSON from Google Cloud Console)
YOUTUBE_CLIENT_SECRET_FILE=data/youtube_client_secret.json

# Safety defaults
DRY_RUN=true
YOUTUBE_PRIVACY_STATUS=private
```

**Get your Drive Folder ID:**
- Open the folder in Google Drive
- Copy the ID from the URL: `https://drive.google.com/drive/folders/[THIS_IS_THE_FOLDER_ID]`

## Usage

### Windows desktop app

Install the project requirements, then launch the PySide6 desktop interface:

```powershell
python -m app.gui_main
```

The desktop UI uses the existing local job runner, settings, account profiles,
and upload pipeline. Video generation and metadata work run in background
threads so the interface remains responsive; uploads still require review and
explicit confirmation.

### Check Configuration

```bash
python -m app check-config
```

This validates your settings and checks for FFmpeg.

### Local Testing (No Google APIs Required)

Process local media files without Google Drive:

```bash
# Place test images/videos in data/input/
python -m app process-local data/input/

# Check output in data/output/
```

This is the recommended first step to verify the media processing pipeline works.

### Scan Google Drive

List files in your configured Drive folder:

```bash
python -m app scan
```

### Run Full Pipeline (DRY_RUN)

Process files from Drive without uploading:

```bash
python -m app run
```

In `DRY_RUN=true` mode:
- ✅ Discovers files from Drive
- ✅ Downloads them
- ✅ Generates captions
- ✅ Renders captions on media
- ✅ Saves to `data/output/`
- ❌ Does NOT upload to YouTube

### Upload to YouTube (Private)

After verifying local output:

```bash
# First test run
python -m app run --upload --no-dry-run

# Videos uploaded as PRIVATE by default
```

### Upload to YouTube (Public) - Requires Confirmation

```bash
# Public upload requires explicit confirmation
python -m app run --upload --no-dry-run --privacy public --confirm-public
```

This safety mechanism prevents accidental public uploads during development.

### View Processing Status

```bash
python -m app status
```

Shows counts by status and recently processed items.

### Retry Failed Items

```bash
python -m app retry-failed
```

Resets failed items to allow reprocessing.

### Force Reprocessing

```bash
# Reprocess everything, ignoring duplicate detection
python -m app run --force
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `scan` | List files in Google Drive folder |
| `process-local [DIR]` | Process local files without Drive |
| `run` | Execute full pipeline (Drive → process → upload) |
| `status` | Show processing statistics |
| `retry-failed` | Reset failed items for retry |
| `check-config` | Validate configuration |

**Common Options:**

| Option | Description |
|--------|-------------|
| `--dry-run` / `--no-dry-run` | Override DRY_RUN setting |
| `--upload` | Enable YouTube upload |
| `--privacy private/unlisted/public` | Set YouTube privacy |
| `--confirm-public` | Required for public uploads |
| `--force` | Force reprocessing |
| `--env-file PATH` | Use custom .env file |

## Configuration Options

See `.env.example` for all available options.

**Key Settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Prevent YouTube uploads |
| `YOUTUBE_PRIVACY_STATUS` | `private` | Default upload privacy |
| `CAPTION_STYLE` | `dark_humor` | Caption style (dark_humor, funny, sarcastic, wholesome) |
| `CAPTION_MAX_LENGTH` | `100` | Maximum caption length |
| `CAPTION_FONT_SIZE` | `48` | Font size for captions |
| `MAX_FILE_SIZE_MB` | `500` | Maximum file size to process |

## Supported Formats

**Images:**
- JPG/JPEG
- PNG
- WebP

**Videos:**
- MP4
- MOV
- WebM
- MKV (if FFmpeg supports)

## Database

The application uses SQLite (`data/automation.sqlite3`) to track processed files.

**Media Item Statuses:**
- `DISCOVERED` - Found in Drive, not yet downloaded
- `DOWNLOADED` - Downloaded locally
- `PROCESSED` - Caption rendered successfully
- `UPLOADED` - Uploaded to YouTube
- `FAILED` - Processing failed
- `SKIPPED` - Skipped (unsupported format, etc.)

The database ensures **idempotent processing** - files are processed only once unless `--force` is used.

## Development

### Run Tests

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app

# Verbose output
pytest -v

# Run specific test file
pytest tests/test_database.py
```

### Project Structure

The codebase follows clean architecture principles:

- **Separation of concerns** - Each module has a single responsibility
- **Dependency injection** - Components receive dependencies via constructors
- **Interface abstractions** - `CaptionGenerator` allows multiple implementations
- **Testable design** - Business logic separated from I/O

## GitHub Actions

The project includes a scheduled workflow for automated runs.

**Setup:**

1. Push repository to GitHub
2. Add secrets in repository settings:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_DRIVE_FOLDER_ID`
   - `YOUTUBE_CLIENT_SECRET_JSON` (entire JSON file contents)
   - Any other required secrets from `.env.example`

3. The workflow runs on schedule and manual trigger

**Workflow Features:**
- Runs tests on push
- Scheduled automation runs
- Uses GitHub Secrets for credentials
- Never exposes secrets in logs

## Safety & Security

### Built-in Safety Features

1. **DRY_RUN Default** - YouTube uploads disabled by default
2. **Public Upload Protection** - Requires `--confirm-public` flag
3. **Duplicate Prevention** - Database tracks processed files
4. **Credential Safety** - No secrets in code or logs
5. **Watermark Preservation** - Does not remove attribution
6. **Source File Protection** - Never overwrites originals

### Security Checklist

- ✅ OAuth tokens stored locally, never committed
- ✅ `.gitignore` excludes all credential files
- ✅ Logs do not contain secrets
- ✅ Environment variables for all sensitive data
- ✅ GitHub Actions uses encrypted secrets

### What Gets Committed

- ✅ Source code
- ✅ Tests
- ✅ Documentation
- ✅ `.env.example` template
- ✅ `.gitkeep` files for directory structure

### What Never Gets Committed

- ❌ `.env` with actual credentials
- ❌ OAuth tokens (`*token*.json`)
- ❌ Client secrets (`*credentials*.json`, `*client_secret*.json`)
- ❌ Database files (`*.sqlite3`)
- ❌ Downloaded/processed media files
- ❌ Log files

## Troubleshooting

### FFmpeg Not Found

```
Error: FFmpeg not found
Solution: Install FFmpeg and ensure it's in your PATH
```

### Google Drive Authentication Failed

```
Error: Failed to authenticate with Google Drive
Solutions:
1. Verify GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are correct
2. Ensure Google Drive API is enabled in Google Cloud Console
3. Delete data/google_token.json and re-authenticate
```

### YouTube Upload Failed

```
Error: Failed to authenticate with YouTube
Solutions:
1. Verify youtube_client_secret.json exists and is valid
2. Ensure YouTube Data API v3 is enabled
3. Delete data/youtube_token.json and re-authenticate
4. Check API quota limits in Google Cloud Console
```

### Font Warnings (Image Processing)

```
Warning: Using default font; install Arial for better results
Solution: This is cosmetic. The app works with the default font.
To use Arial: Ensure arial.ttf or Arial.ttf is in your system fonts directory.
```

### No Files Discovered

```
No files found in Drive folder
Solutions:
1. Verify GOOGLE_DRIVE_FOLDER_ID is correct
2. Check folder permissions (must have read access)
3. Verify folder contains supported file formats
4. Run: python -m app scan
```

## Limitations

- **GitHub Actions** is not a 24/7 server; it's ephemeral/scheduled
- **OAuth tokens** expire and require periodic re-authentication
- **YouTube API** has daily quota limits
- **FFmpeg** required for video processing
- **Template captions** are pre-written (LLM integration not yet implemented)

## Future Enhancements

- [ ] LLM-based caption generation
- [ ] Configurable caption positioning
- [ ] Video thumbnail generation
- [ ] Batch upload optimization
- [ ] Web dashboard for monitoring
- [ ] Advanced caption styling options
- [ ] Multi-folder support

## License

This is a test project for educational and development purposes.

## Contributing

This is currently a personal test project. If you fork it:

1. Respect Google's API Terms of Service
2. Respect YouTube's Community Guidelines
3. Never remove third-party watermarks/attribution
4. Test thoroughly before public uploads

## Support

For issues or questions:
1. Check this README thoroughly
2. Review `.env.example` for configuration options
3. Run `python -m app check-config` to validate setup
4. Check logs in `logs/automation.log`

---

**Remember:** Always test with `DRY_RUN=true` and `--privacy private` before attempting public uploads.
