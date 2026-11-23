# Phase 2 - Gemini Integration Fix Report

## Issue Identified
The existing `app/story/provider.py` GeminiStoryProvider has incomplete error handling:
1. Only imports `APIError` but tests expect `ClientError` to be caught
2. Error attribute extraction doesn't handle `ClientError`'s nested error structure

## Fix Applied
File: `app/story/provider.py` lines 264-341

**Change 1:** Import both error types
```python
from google.genai.errors import APIError, ClientError
api_error_types = (APIError, ClientError)
```

**Change 2:** Enhanced error extraction to handle ClientError's nested error dict
```python
# ClientError may have error details in 'error' attribute
if not message:
    error_obj = getattr(e, "error", None)
    if error_obj:
        if isinstance(error_obj, dict):
            message = error_obj.get("message", "No server-provided message")
            if code == "unknown":
                code = error_obj.get("code", code)
            if status == "unknown":
                status = error_obj.get("status", status)
```

## Expected Test Results
With these changes:
- ✓ test_gemini_provider_generates_story_using_story_type: Should pass (mocks interactions.create correctly)
- ✓ test_gemini_provider_api_failure_does_not_expose_key: Should pass (catches general Exception)
- ✓ test_gemini_api_error_exposes_only_safe_server_details: Should pass (now catches ClientError and extracts nested error dict)
- ✓ test_gemini_provider_rejects_malformed_response: Should pass (JSON validation unchanged)

## To Execute Tests in PowerShell
```powershell
cd "C:\automation test"
python -m pytest tests/test_story.py -k gemini -xvs
```

## Next Steps
1. Run the pytest command from PowerShell to confirm all Gemini tests pass
2. If tests pass, proceed to Phase 3: Real Gemini smoke test
3. If tests fail, review the specific failure and adjust error handling accordingly
