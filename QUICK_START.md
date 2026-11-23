# Quick Start Guide

## Immediate Testing (No API Setup Required)

### 1. Install Dependencies

```bash
# Navigate to project
cd "C:\automation test"

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 2. Install FFmpeg

**Windows (Chocolatey):**
```bash
choco install ffmpeg
```

**Or download from:** https://ffmpeg.org/download.html

**Verify:**
```bash
ffmpeg -version
```

### 3. Test Local Processing (Fastest Way to See It Work)

```bash
# Create a test image (or copy your own to data/input/)
# Then run:
python -m app process-local data/input/

# Check data/output/ for processed images with captions!
```

### 4. Check Configuration

```bash
python -m app check-config
```

### 5. Run Tests

```bash
pytest -v
```

---

## Full Setup (With Google APIs)

### Step 1: Google Cloud Setup

1. Go to https://console.cloud.google.com
2. Create a new project
3. Enable APIs:
   - Google Drive API
   - YouTube Data API v3
4. Create OAuth 2.0 credentials (Desktop application)
5. Download client secret JSON

### Step 2: Configure Environment

```bash
# Copy template
cp .env.example .env

# Edit with your values
notepad .env
```

**Required variables:**
```env
GOOGLE_CLIENT_ID=your_id_here
GOOGLE_CLIENT_SECRET=your_secret_here
GOOGLE_DRIVE_FOLDER_ID=your_folder_id_here
YOUTUBE_CLIENT_SECRET_FILE=data/youtube_client_secret.json

DRY_RUN=true  # Keep this for safety!
```

### Step 3: Get Drive Folder ID

1. Open your Drive folder in browser
2. Copy ID from URL: `https://drive.google.com/drive/folders/[THIS_IS_THE_ID]`

### Step 4: Run Full Pipeline

```bash
# DRY_RUN mode (no YouTube upload)
python -m app run

# Check output
python -m app status
```

---

## CLI Commands Quick Reference

| Command | What It Does |
|---------|--------------|
| `process-local data/input/` | Process local files (no Drive needed) |
| `scan` | List files in Drive folder |
| `run` | Full pipeline in DRY_RUN mode |
| `run --upload --no-dry-run` | Process and upload (private) |
| `status` | Show processing statistics |
| `retry-failed` | Retry failed items |
| `check-config` | Validate configuration |

---

## Troubleshooting

**"Python not found"**
- Install Python 3.11+ from python.org
- Check PATH

**"FFmpeg not found"**
- Install FFmpeg (see above)
- Restart terminal

**"Tests fail"**
- Activate virtual environment first
- Run: `pip install -r requirements.txt`

**"Google authentication failed"**
- Check CLIENT_ID and CLIENT_SECRET in .env
- Ensure APIs are enabled in Google Cloud Console
- Delete data/*token*.json and try again

---

## Safety Checklist Before First Run

- [ ] DRY_RUN=true in .env
- [ ] .env file NOT committed to git
- [ ] FFmpeg installed
- [ ] Virtual environment activated
- [ ] Dependencies installed
- [ ] Test with `process-local` first
- [ ] Check output in data/output/

---

## Next Steps

1. **Test locally** with sample images/videos
2. **Configure Google Drive** when ready
3. **Run in DRY_RUN mode** first
4. **Upload private videos** to test YouTube
5. **Only then** consider public uploads

**Remember:** Start small, test incrementally, stay safe!
