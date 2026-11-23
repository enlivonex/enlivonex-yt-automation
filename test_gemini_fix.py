#!/usr/bin/env python
"""Quick test of Gemini error handling fixes."""
import sys
import json
from unittest.mock import Mock

# Test 1: Verify imports work
try:
    from google.genai.errors import APIError, ClientError
    print("✓ Successfully imported APIError and ClientError")
except ImportError as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)

# Test 2: Verify GeminiStoryProvider catches ClientError
try:
    from app.story.provider import GeminiStoryProvider
    print("✓ Successfully imported GeminiStoryProvider")
except ImportError as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)

# Test 3: Mock ClientError and verify error handling
try:
    api_key = "test-secret-gemini-key"
    client = Mock()
    client.interactions.create.side_effect = ClientError(
        404,
        {
            "error": {
                "code": 404,
                "status": "NOT_FOUND",
                "message": f"Requested resource unavailable ({api_key})",
            }
        },
    )
    provider = GeminiStoryProvider(api_key, client=client)

    try:
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")
        print("✗ Should have raised RuntimeError")
        sys.exit(1)
    except RuntimeError as error:
        error_str = str(error)
        if api_key in error_str:
            print(f"✗ API key exposed in error: {error_str}")
            sys.exit(1)
        if "404" not in error_str or "NOT_FOUND" not in error_str:
            print(f"✗ Error doesn't contain HTTP code/status: {error_str}")
            sys.exit(1)
        print(f"✓ ClientError handled correctly: {error_str}")
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: Mock APIError and verify error handling
try:
    api_key = "test-secret-gemini-key"
    client = Mock()
    client.interactions.create.side_effect = APIError(
        "Internal server error"
    )
    provider = GeminiStoryProvider(api_key, client=client)

    try:
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")
        print("✗ Should have raised RuntimeError")
        sys.exit(1)
    except RuntimeError as error:
        error_str = str(error)
        if api_key in error_str:
            print(f"✗ API key exposed in error: {error_str}")
            sys.exit(1)
        print(f"✓ APIError handled correctly: {error_str}")
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: Verify successful response parsing
try:
    client = Mock()
    response_data = {
        "title": "A Small Reminder",
        "hook": "Small steps still count.",
        "segments": [
            {"text": "Small steps still count.", "start_time": 0.0, "end_time": 3.0},
            {"text": "Keep moving at your own pace.", "start_time": 3.0, "end_time": 15.0},
        ],
        "total_duration": 15.0,
        "tone": "casual",
        "target_audience": "general",
    }
    client.interactions.create.return_value.output_text = json.dumps(response_data)
    provider = GeminiStoryProvider("test-key", model="gemini-3.8-flash", client=client)

    story = provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape", target_duration=15.0)
    if story.title == "A Small Reminder" and story.total_duration == 15.0:
        print("✓ Successful response parsed correctly")
    else:
        print(f"✗ Response parsing failed: {story}")
        sys.exit(1)
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✓ All Gemini error handling tests passed!")
