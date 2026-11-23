#!/usr/bin/env python
"""
Phase 3: Gemini Smoke Test
Tests real Gemini story generation with one minimal request.
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.config.settings import load_settings
from app.story.provider import GeminiStoryProvider

print("=" * 60)
print("GEMINI STORY GENERATION SMOKE TEST")
print("=" * 60)

# Load settings
try:
    settings = load_settings()
    print(f"✓ Settings loaded")
    print(f"  STORY_PROVIDER: {settings.story_provider}")
    print(f"  GEMINI_STORY_MODEL: {settings.gemini_story_model}")

    if not settings.gemini_api_key or not settings.gemini_api_key.strip():
        print("✗ GEMINI_API_KEY not configured")
        sys.exit(1)

    # Verify key exists without printing it
    key_prefix = settings.gemini_api_key[:8] if len(settings.gemini_api_key) >= 8 else "***"
    print(f"  GEMINI_API_KEY: {key_prefix}... (configured)")

except Exception as e:
    print(f"✗ Failed to load settings: {e}")
    sys.exit(1)

# Create provider
try:
    provider = GeminiStoryProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_story_model
    )
    print(f"✓ GeminiStoryProvider initialized")
except Exception as e:
    print(f"✗ Failed to initialize provider: {e}")
    sys.exit(1)

# Test story generation with minimal clip metadata
print("\n" + "=" * 60)
print("MAKING REAL GEMINI API REQUEST")
print("This will consume API quota")
print("=" * 60)

clip_description = "Video clip: MINECRAFT PARKOUR GAMEPLAY NO COPYRIGHT 2K_720p60.mp4, duration: 21.9s, portrait"
target_duration = 15.0

try:
    print(f"\nClip: {clip_description}")
    print(f"Target duration: {target_duration}s")
    print(f"Requesting story generation...")

    story = provider.generate_story(
        clip_description=clip_description,
        target_duration=target_duration,
        tone="casual"
    )

    print("\n" + "=" * 60)
    print("✓ SUCCESS - Story Generated")
    print("=" * 60)
    print(f"Title: {story.title}")
    print(f"Hook: {story.hook}")
    print(f"Total Duration: {story.total_duration}s")
    print(f"Segments: {len(story.segments)}")
    print(f"Tone: {story.tone}")
    print(f"Target Audience: {story.target_audience}")

    print("\nSegment breakdown:")
    for i, seg in enumerate(story.segments, 1):
        duration = seg.end_time - seg.start_time
        print(f"  {i}. [{seg.start_time:.1f}s - {seg.end_time:.1f}s] ({duration:.1f}s)")
        print(f"     Text: {seg.text}")
        if seg.emphasis:
            print(f"     Emphasis: {seg.emphasis}")

    # Validate story structure
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)

    validation_passed = True

    if not story.title or not story.title.strip():
        print("✗ Title is empty")
        validation_passed = False
    else:
        print("✓ Title is valid")

    if not story.hook or not story.hook.strip():
        print("✗ Hook is empty")
        validation_passed = False
    else:
        print("✓ Hook is valid")

    if not story.segments or len(story.segments) == 0:
        print("✗ No segments")
        validation_passed = False
    else:
        print(f"✓ Has {len(story.segments)} segments")

    for i, seg in enumerate(story.segments, 1):
        if not seg.text or not seg.text.strip():
            print(f"✗ Segment {i} has empty text")
            validation_passed = False

    if validation_passed:
        print(f"✓ All segments have text")

    # Check timing
    if story.segments:
        first_start = story.segments[0].start_time
        last_end = story.segments[-1].end_time
        total_span = last_end - first_start

        if first_start != 0.0:
            print(f"⚠ First segment starts at {first_start}s (expected 0.0s)")
        else:
            print("✓ First segment starts at 0.0s")

        tolerance = 5.0  # Allow 5 second tolerance
        if abs(total_span - target_duration) > tolerance:
            print(f"⚠ Story span {total_span:.1f}s differs from target {target_duration}s by more than {tolerance}s")
        else:
            print(f"✓ Story span {total_span:.1f}s is close to target {target_duration}s")

    # Check for visual claims (should not assert visual facts)
    full_text = story.full_text.lower()
    suspicious_phrases = ["i see", "we can see", "you can see", "the video shows", "watch as", "look at"]
    visual_claims_found = []
    for phrase in suspicious_phrases:
        if phrase in full_text:
            visual_claims_found.append(phrase)

    if visual_claims_found:
        print(f"⚠ Story may contain visual claims: {visual_claims_found}")
        print("  (Reminder: prompts specify metadata only, no visual analysis)")
    else:
        print("✓ No obvious visual claims detected")

    if validation_passed:
        print("\n" + "=" * 60)
        print("✓✓✓ GEMINI SMOKE TEST PASSED ✓✓✓")
        print("=" * 60)

        # Save story for inspection
        output_file = Path("data/temp/gemini_smoke_test_story.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(story.to_dict(), f, indent=2)
        print(f"\nStory saved to: {output_file}")

        sys.exit(0)
    else:
        print("\n✗ Validation failed")
        sys.exit(1)

except RuntimeError as e:
    error_msg = str(e)
    print("\n" + "=" * 60)
    print("✗ GEMINI API ERROR")
    print("=" * 60)
    print(f"Error: {error_msg}")

    # Classify error type
    if "404" in error_msg or "NOT_FOUND" in error_msg:
        print("\nDiagnosis: Model or resource not found")
        print("  - Check that model 'gemini-3.8-flash' is available")
        print("  - Verify API access permissions")
    elif "401" in error_msg or "UNAUTHENTICATED" in error_msg:
        print("\nDiagnosis: Authentication failed")
        print("  - Verify GEMINI_API_KEY is correct")
        print("  - Check that key has not been revoked")
    elif "403" in error_msg or "PERMISSION_DENIED" in error_msg:
        print("\nDiagnosis: Permission denied")
        print("  - Verify API key has access to Gemini API")
        print("  - Check billing/quota limits")
    elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
        print("\nDiagnosis: Quota exceeded")
        print("  - API rate limit or quota exhausted")
        print("  - Wait and retry later")
    elif "500" in error_msg or "503" in error_msg or "INTERNAL" in error_msg:
        print("\nDiagnosis: Server error")
        print("  - Transient server issue")
        print("  - Retry may succeed")
    else:
        print("\nDiagnosis: Unknown error")
        print("  - Check network connectivity")
        print("  - Review error details above")

    sys.exit(2)

except Exception as e:
    print("\n" + "=" * 60)
    print("✗ UNEXPECTED ERROR")
    print("=" * 60)
    print(f"Error type: {type(e).__name__}")
    print(f"Error: {e}")

    import traceback
    print("\nTraceback:")
    traceback.print_exc()

    sys.exit(3)
