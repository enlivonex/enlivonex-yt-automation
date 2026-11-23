"""
Daily batch script: Generate exactly 2 YouTube Shorts per day at ₹0 cost.

Features:
- Free offline TTS (Windows SAPI voices via pyttsx3)
- Optional Gemini story generation (free tier) with local fallback
- 2 distinct videos per run
- Prevents duplicate generation
- Logs all operations
- Never uses mock/silent audio
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, date

sys.path.insert(0, str(Path(__file__).parent))

from app.config.settings import load_settings
from app.database.db import Database
from app.clips.manager import ClipManager
from app.clips.selector import LeastUsedSelector
from app.story.provider import GeminiStoryProvider, TemplateStoryProvider
from app.voiceover.offline_provider import OfflineTTSProvider
from app.voiceover.models import VoiceConfig
from app.metadata.generator import TemplateMetadataGenerator
from app.orchestration.job import JobOrchestrator
from app.utils.ffmpeg import FFmpegWrapper

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"daily_batch_{date.today().isoformat()}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def check_daily_completion():
    """Check if today's batch is already complete."""
    DB_PATH = Path("data/media.db")
    today = date.today().isoformat()

    with Database(DB_PATH) as db:
        db.initialize()
        cursor = db.conn.cursor()

        # Count completed jobs from today
        cursor.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE date(started_at) = ?
            AND status = 'COMPLETED'
        """, (today,))

        completed_today = cursor.fetchone()[0]

        return completed_today >= 2, completed_today

def generate_one_short(job_number, story_provider, voice_provider, orchestrator, selector, voice_config):
    """Generate one complete YouTube Short."""
    logger.info(f"=== Starting Job {job_number} ===")

    try:
        with orchestrator.db:
            job_id = orchestrator.create_job(clip_selector=selector)
            logger.info(f"Created job: {job_id}")

            result = orchestrator.run_job(
                job_id,
                clip_selector=selector,
                voice_config=voice_config,
                dry_run=False
            )

            output_video = Path(result['artifacts_dir']) / "output.mp4"

            if not output_video.exists():
                raise RuntimeError(f"Output video not found: {output_video}")

            # Validate output
            ffmpeg = FFmpegWrapper()
            info = ffmpeg.probe(output_video)
            has_audio = ffmpeg.has_audio_stream(output_video)

            file_size_mb = output_video.stat().st_size / 1024 / 1024

            logger.info(f"✓ Job {job_number} completed successfully")
            logger.info(f"  Video: {output_video}")
            logger.info(f"  Duration: {info.duration:.2f}s")
            logger.info(f"  Resolution: {info.width}x{info.height}")
            logger.info(f"  Audio: {'Present' if has_audio else 'MISSING'}")
            logger.info(f"  Size: {file_size_mb:.2f} MB")

            if not has_audio:
                raise RuntimeError("Video has no audio stream!")

            return {
                'success': True,
                'job_id': job_id,
                'output': str(output_video),
                'duration': info.duration,
                'size_mb': file_size_mb
            }

    except Exception as e:
        logger.error(f"✗ Job {job_number} failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

def main():
    logger.info("=" * 70)
    logger.info("DAILY YOUTUBE SHORTS BATCH - ₹0 AUTOMATION")
    logger.info(f"Date: {date.today().isoformat()}")
    logger.info(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    logger.info("=" * 70)

    # Check if already completed today
    is_complete, completed_count = check_daily_completion()

    if is_complete:
        logger.info(f"✓ Daily batch already completed ({completed_count} videos generated today)")
        logger.info("Exiting to prevent duplicates")
        return 0

    logger.info(f"Status: {completed_count}/2 videos completed today")
    logger.info(f"Need to generate: {2 - completed_count} more video(s)")

    # Load settings
    settings = load_settings()
    logger.info(f"Settings loaded: STORY_PROVIDER={settings.story_provider}, TTS_PROVIDER={settings.tts_provider}")

    # Initialize story provider
    if settings.story_provider == "gemini" and settings.gemini_api_key and settings.gemini_api_key.strip():
        logger.info("Using Gemini for story generation (free tier)")
        try:
            story_provider = GeminiStoryProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_story_model
            )
        except Exception as e:
            logger.warning(f"Gemini initialization failed: {e}")
            logger.info("Falling back to template story provider")
            story_provider = TemplateStoryProvider()
    else:
        logger.info("Using template story provider (offline, free)")
        story_provider = TemplateStoryProvider()

    # Initialize offline TTS
    logger.info("Initializing offline TTS (pyttsx3 + Windows SAPI)")
    try:
        voice_provider = OfflineTTSProvider()
        voice_config = VoiceConfig(provider="offline", voice_id="default")
        logger.info("✓ Offline TTS initialized")
    except RuntimeError as e:
        logger.error(f"Failed to initialize offline TTS: {e}")
        logger.error("Install pyttsx3: pip install pyttsx3")
        return 1

    # Initialize components
    DB_PATH = Path("data/media.db")
    CLIPS_DIR = Path("data/clips")
    JOBS_DIR = Path("data/jobs")

    db = Database(DB_PATH)
    with db:
        db.initialize()

    clip_manager = ClipManager(db, CLIPS_DIR)
    metadata_generator = TemplateMetadataGenerator()

    orchestrator = JobOrchestrator(
        db=db,
        clip_manager=clip_manager,
        story_provider=story_provider,
        voice_provider=voice_provider,
        metadata_generator=metadata_generator,
        work_dir=JOBS_DIR
    )

    selector = LeastUsedSelector()

    # Generate required number of videos
    results = []
    videos_to_generate = 2 - completed_count

    for i in range(videos_to_generate):
        job_num = completed_count + i + 1
        result = generate_one_short(
            job_num,
            story_provider,
            voice_provider,
            orchestrator,
            selector,
            voice_config
        )
        results.append(result)

        if not result['success']:
            logger.error(f"Job {job_num} failed, continuing with next job...")

    # Summary
    logger.info("=" * 70)
    logger.info("BATCH SUMMARY")
    logger.info("=" * 70)

    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]

    logger.info(f"Successful: {len(successful)}")
    logger.info(f"Failed: {len(failed)}")

    if successful:
        total_size = sum(r['size_mb'] for r in successful)
        total_duration = sum(r['duration'] for r in successful)

        logger.info(f"\nGenerated videos:")
        for i, r in enumerate(successful, 1):
            logger.info(f"  {i}. {r['output']}")
            logger.info(f"     Job: {r['job_id']}, Duration: {r['duration']:.1f}s, Size: {r['size_mb']:.2f}MB")

        logger.info(f"\nTotal disk usage: {total_size:.2f} MB")
        logger.info(f"Total video duration: {total_duration:.1f}s")

    if failed:
        logger.warning(f"\nFailed jobs:")
        for i, r in enumerate(failed, 1):
            logger.warning(f"  {i}. Error: {r['error']}")

    logger.info(f"\nLog file: {log_file}")
    logger.info("=" * 70)

    # Return exit code based on success
    if len(successful) == videos_to_generate:
        logger.info("✓ Batch completed successfully")
        return 0
    elif len(successful) > 0:
        logger.warning("⚠ Batch partially completed")
        return 2
    else:
        logger.error("✗ Batch failed completely")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
