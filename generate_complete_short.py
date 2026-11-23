#!/usr/bin/env python
"""
Complete end-to-end YouTube Shorts generation with real Gemini story and best available TTS.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.config.settings import load_settings
from app.config.ai_providers import create_ai_providers, ProviderConfigurationError
from app.database.db import Database
from app.clips.manager import ClipManager
from app.clips.selector import LeastUsedSelector
from app.orchestration.job import JobOrchestrator
from app.metadata.generator import TemplateMetadataGenerator
from app.utils.ffmpeg import FFmpegWrapper

print("=" * 70)
print("COMPLETE YOUTUBE SHORTS GENERATION")
print("=" * 70)

# Load settings
settings = load_settings()
print(f"\n✓ Settings loaded")
print(f"  Story Provider: {settings.story_provider}")
print(f"  TTS Provider: {settings.tts_provider}")

# Check TTS configuration
tts_status = settings.ai_provider_status()
print(f"  Story status: {tts_status['story']}")
print(f"  TTS status: {tts_status['tts']}")

if settings.tts_provider == "mock":
    print("\n" + "!" * 70)
    print("WARNING: TTS_PROVIDER is set to 'mock'")
    print("This will generate SILENT audio, not real narration.")
    print("!" * 70)

    # Check for available real TTS providers
    print("\nChecking for available real TTS providers:")

    has_openai = bool(settings.openai_api_key and settings.openai_api_key.strip())
    has_elevenlabs = bool(settings.elevenlabs_api_key and settings.elevenlabs_api_key.strip()
                         and settings.elevenlabs_voice_id and settings.elevenlabs_voice_id.strip())

    print(f"  OpenAI TTS: {'✓ Available (key configured)' if has_openai else '✗ Not configured (OPENAI_API_KEY missing)'}")
    print(f"  ElevenLabs TTS: {'✓ Available (key + voice configured)' if has_elevenlabs else '✗ Not configured (ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID missing)'}")

    if not has_openai and not has_elevenlabs:
        print("\n" + "=" * 70)
        print("BLOCKER: No real TTS provider configured")
        print("=" * 70)
        print("\nTo enable real narration, add ONE of the following to .env:")
        print("\nOption 1 - OpenAI TTS (recommended, simple):")
        print("  TTS_PROVIDER=openai")
        print("  OPENAI_API_KEY=sk-your-key-here")
        print("  (Uses voice 'alloy' by default)")
        print("\nOption 2 - ElevenLabs TTS:")
        print("  TTS_PROVIDER=elevenlabs")
        print("  ELEVENLABS_API_KEY=your-key-here")
        print("  ELEVENLABS_VOICE_ID=your-voice-id-here")
        print("\nAfter adding credentials, run this script again.")
        print("=" * 70)
        sys.exit(1)

    # Auto-select first available provider
    if has_openai:
        print("\n→ Auto-switching to OpenAI TTS (OPENAI_API_KEY detected)")
        settings.tts_provider = "openai"
    elif has_elevenlabs:
        print("\n→ Auto-switching to ElevenLabs TTS (credentials detected)")
        settings.tts_provider = "elevenlabs"

# Initialize AI providers
try:
    ai_providers = create_ai_providers(settings)
    print(f"✓ AI providers initialized")
    print(f"  Story: {type(ai_providers.story).__name__}")
    print(f"  Voice: {type(ai_providers.voice).__name__}")
    print(f"  Voice config: {ai_providers.voice_config.provider} / {ai_providers.voice_config.voice_id}")
except ProviderConfigurationError as e:
    print(f"\n✗ Provider configuration error: {e}")
    sys.exit(1)

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
    story_provider=ai_providers.story,
    voice_provider=ai_providers.voice,
    metadata_generator=metadata_generator,
    work_dir=JOBS_DIR
)

print(f"✓ Components initialized")

# Check for available clips
with db:
    unused_clips = clip_manager.get_unused_clips(limit=10)
    if not unused_clips:
        print("\n✗ No unused clips available")
        print("Run: python -m app.cli_main ingest <directory>")
        sys.exit(1)

    print(f"\n✓ Found {len(unused_clips)} unused clips")
    print(f"  Using: {unused_clips[0].filename} ({unused_clips[0].duration_seconds:.1f}s, {unused_clips[0].orientation})")

# Create and run job
print("\n" + "=" * 70)
print("CREATING JOB")
print("=" * 70)

selector = LeastUsedSelector()
voice_config = ai_providers.voice_config

try:
    with db:
        job_id = orchestrator.create_job(clip_selector=selector)
        print(f"✓ Job created: {job_id}")

        print("\n" + "=" * 70)
        print("RUNNING PIPELINE")
        print("=" * 70)
        print("Stages: SELECT_CLIP → STORY → VOICEOVER → CAPTIONS → RENDER → METADATA")
        print()

        result = orchestrator.run_job(
            job_id,
            clip_selector=selector,
            voice_config=voice_config,
            dry_run=False  # Generate full video
        )

        print("\n" + "=" * 70)
        print("✓✓✓ JOB COMPLETED ✓✓✓")
        print("=" * 70)
        print(f"Job ID: {result['job_id']}")
        print(f"Source clip: {result['clip']}")
        print(f"Story title: {result['story_title']}")
        print(f"Output directory: {result['artifacts_dir']}")

        # Validate output
        output_video = Path(result['artifacts_dir']) / "output.mp4"

        if not output_video.exists():
            print(f"\n✗ Output video not found: {output_video}")
            sys.exit(1)

        print(f"\n✓ Output video exists: {output_video}")

        # FFprobe validation
        print("\n" + "=" * 70)
        print("VALIDATING OUTPUT")
        print("=" * 70)

        ffmpeg = FFmpegWrapper()

        try:
            info = ffmpeg.probe(output_video)
            print(f"✓ Video readable by FFprobe")
            print(f"  Duration: {info.duration:.2f}s")
            print(f"  Dimensions: {info.width}x{info.height}")
            print(f"  Orientation: {info.orientation}")
            print(f"  FPS: {info.fps}")
            print(f"  Codec: {info.codec}")

            # Check orientation
            if info.width == 1080 and info.height == 1920:
                print(f"✓ Correct portrait orientation (1080x1920)")
            elif info.height > info.width:
                print(f"✓ Portrait orientation ({info.width}x{info.height})")
            else:
                print(f"⚠ Not portrait: {info.width}x{info.height}")

            # Check audio
            has_audio = ffmpeg.has_audio_stream(output_video)
            if has_audio:
                print(f"✓ Audio stream present")
            else:
                print(f"✗ No audio stream found")

            # Get audio duration
            try:
                # Check for merged audio file
                job_dir = Path(result['artifacts_dir'])
                audio_files = list(job_dir.glob("audio/*.mp3"))

                if audio_files:
                    print(f"\n  Found {len(audio_files)} audio segments")
                    for i, audio_file in enumerate(audio_files[:3], 1):
                        try:
                            duration = ffmpeg.probe_duration(audio_file)
                            print(f"    Segment {i}: {audio_file.name} ({duration:.2f}s)")
                        except:
                            pass
            except Exception as e:
                print(f"  (Could not check audio segments: {e})")

        except Exception as e:
            print(f"✗ FFprobe validation failed: {e}")
            sys.exit(1)

        print("\n" + "=" * 70)
        print("SUCCESS - Complete YouTube Short Generated")
        print("=" * 70)
        print(f"\nFinal output: {output_video}")
        print(f"File size: {output_video.stat().st_size / 1024 / 1024:.2f} MB")
        print("\nNext steps:")
        print(f"  1. Review video: {output_video}")
        print(f"  2. Check story: {job_dir}/story.json")
        print(f"  3. Check metadata: {job_dir}/metadata.json")
        print(f"  4. Verify narration is audible (requires manual playback)")

        sys.exit(0)

except Exception as e:
    print(f"\n✗ Job failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
