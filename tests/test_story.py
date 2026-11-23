"""Tests for story generation."""

import pytest
import json
from types import SimpleNamespace
from app.story.models import Story, StorySegment
from unittest.mock import Mock
from app.story.provider import (
    GeminiStoryProvider,
    OpenAIStoryProvider,
    TemplateStoryProvider,
)


def test_story_segment_creation():
    """Test creating a story segment."""
    seg = StorySegment(
        text="This is a test segment.",
        start_time=0.0,
        end_time=5.0,
        emphasis="excited"
    )

    assert seg.text == "This is a test segment."
    assert seg.start_time == 0.0
    assert seg.end_time == 5.0
    assert seg.emphasis == "excited"


def test_story_creation():
    """Test creating a complete story."""
    segments = [
        StorySegment("Hook line", 0.0, 3.0, "excited"),
        StorySegment("Main content", 3.0, 55.0, None),
        StorySegment("Outro", 55.0, 60.0, "friendly")
    ]

    story = Story(
        title="Test Story",
        hook="Hook line",
        segments=segments,
        total_duration=60.0,
        tone="casual",
        target_audience="general"
    )

    assert story.title == "Test Story"
    assert story.hook == "Hook line"
    assert len(story.segments) == 3
    assert story.total_duration == 60.0
    assert story.tone == "casual"


def test_template_provider_uses_the_selected_niche():
    story = TemplateStoryProvider().generate_story(
        clip_description="",
        target_duration=30,
        constraints={"niche": "fitness", "target_word_count": 75},
    )

    assert "fitness" in story.title.lower()
    assert "fitness" in story.full_text.lower()
    assert len(story.full_text.split()) >= 75


def test_story_full_text():
    """Test getting full story text."""
    segments = [
        StorySegment("First line.", 0.0, 3.0),
        StorySegment("Second line.", 3.0, 6.0),
        StorySegment("Third line.", 6.0, 10.0)
    ]

    story = Story(
        title="Test",
        hook="First line.",
        segments=segments,
        total_duration=10.0,
        tone="casual"
    )

    full_text = story.full_text
    assert "First line." in full_text
    assert "Second line." in full_text
    assert "Third line." in full_text


def test_story_to_dict():
    """Test story serialization to dict."""
    segments = [StorySegment("Test", 0.0, 5.0, "calm")]

    story = Story(
        title="Test Story",
        hook="Test",
        segments=segments,
        total_duration=5.0,
        tone="casual",
        target_audience="general"
    )

    data = story.to_dict()

    assert data["title"] == "Test Story"
    assert data["hook"] == "Test"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Test"
    assert data["total_duration"] == 5.0
    assert data["tone"] == "casual"


def test_story_from_dict():
    """Test story deserialization from dict."""
    data = {
        "title": "Test Story",
        "hook": "Hook",
        "segments": [
            {"text": "Segment 1", "start_time": 0.0, "end_time": 3.0, "emphasis": "excited"},
            {"text": "Segment 2", "start_time": 3.0, "end_time": 6.0, "emphasis": None}
        ],
        "total_duration": 6.0,
        "tone": "dramatic",
        "target_audience": "adults"
    }

    story = Story.from_dict(data)

    assert story.title == "Test Story"
    assert story.hook == "Hook"
    assert len(story.segments) == 2
    assert story.segments[0].text == "Segment 1"
    assert story.segments[0].emphasis == "excited"
    assert story.total_duration == 6.0
    assert story.tone == "dramatic"


def test_story_roundtrip_serialization():
    """Test story serialization roundtrip."""
    segments = [
        StorySegment("Line 1", 0.0, 3.0, "excited"),
        StorySegment("Line 2", 3.0, 10.0, "calm")
    ]

    original = Story(
        title="Original Story",
        hook="Line 1",
        segments=segments,
        total_duration=10.0,
        tone="educational",
        target_audience="students"
    )

    # Serialize and deserialize
    data = original.to_dict()
    restored = Story.from_dict(data)

    assert restored.title == original.title
    assert restored.hook == original.hook
    assert len(restored.segments) == len(original.segments)
    assert restored.segments[0].text == original.segments[0].text
    assert restored.total_duration == original.total_duration
    assert restored.tone == original.tone


def test_template_provider():
    """Test template-based story generation."""
    provider = TemplateStoryProvider()

    story = provider.generate_story(
        clip_description="A beautiful sunset over the ocean",
        target_duration=30.0,
        tone="peaceful"
    )

    assert story is not None
    assert story.total_duration == 30.0
    assert story.tone == "peaceful"
    assert len(story.segments) == 3
    assert story.segments[0].start_time == 0.0
    assert story.segments[-1].end_time == 30.0


def test_template_provider_with_constraints():
    """Test template provider with constraints."""
    provider = TemplateStoryProvider()

    story = provider.generate_story(
        clip_description="An exciting sports moment",
        target_duration=45.0,
        tone="energetic",
        constraints={"style": "fast-paced", "niche": "home gardening"}
    )

    assert story.total_duration == 45.0
    assert story.tone == "energetic"
    assert len(story.segments) > 0
    assert "home gardening" in story.full_text
    assert "Home Gardening" in story.title


def test_openai_provider_generates_story_from_metadata_without_claiming_visual_analysis():
    client = Mock()
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({
        "title": "A Small Reminder",
        "hook": "Small steps still count.",
        "segments": [
            {"text": "Small steps still count.", "start_time": 0.0, "end_time": 3.0},
            {"text": "Keep moving at your own pace.", "start_time": 3.0, "end_time": 15.0},
        ],
        "total_duration": 15.0,
        "tone": "casual",
        "target_audience": "general",
        })
    ))])
    client.chat.completions.create.return_value = response
    provider = OpenAIStoryProvider("test-key", client=client)

    story = provider.generate_story(
        "Video clip: parkour_gameplay.mp4, duration: 21.9s, portrait",
        target_duration=15.0,
    )

    assert story.total_duration == 15.0
    request = client.chat.completions.create.call_args.kwargs
    prompt = "\n".join(
        message["content"] for message in request["messages"]
    )
    assert "visual analysis" in prompt
    assert "Do not claim to see" in prompt
    assert "15.0-second" in prompt


def gemini_story_response():
    return json.dumps({
        "title": "A Small Reminder",
        "hook": "Small steps still count.",
        "segments": [
            {"text": "Small steps still count.", "start_time": 0.0, "end_time": 3.0},
            {"text": "Keep moving at your own pace.", "start_time": 3.0, "end_time": 15.0},
        ],
        "total_duration": 15.0,
        "tone": "casual",
        "target_audience": "general",
    })


def test_gemini_provider_generates_story_using_story_type():
    client = Mock()
    client.interactions.create.return_value.output_text = gemini_story_response()
    provider = GeminiStoryProvider("test-gemini-key", model="gemini-test", client=client)

    story = provider.generate_story(
        "Video clip: parkour_gameplay.mp4, duration: 21.9s, portrait",
        target_duration=15.0,
    )

    assert isinstance(story, Story)
    assert story.title == "A Small Reminder"
    assert story.total_duration == 15.0
    call = client.interactions.create.call_args.kwargs
    assert call["model"] == "gemini-test"
    assert "not visual analysis" in call["input"]
    assert "unrelated b-roll" in call["input"]
    assert call["response_format"]["type"] == "text"
    assert call["response_format"]["mime_type"] == "application/json"
    assert (
        call["response_format"]["schema"]["properties"]["segments"]["type"]
        == "array"
    )
    assert call["generation_config"]["temperature"] == 0.7
    assert call["timeout"] == 90.0
    client.models.generate_content.assert_not_called()


def test_gemini_client_uses_bounded_timeout_without_retries(monkeypatch):
    from google import genai

    created = {}

    def make_client(**kwargs):
        created.update(kwargs)
        return Mock()

    monkeypatch.setattr(genai, "Client", make_client)
    GeminiStoryProvider("test-key")

    http_options = created["http_options"]
    assert http_options.timeout == 90_000
    assert http_options.retry_options.attempts == 1


def test_gemini_prompt_includes_niche_target_and_recent_story_avoidance():
    client = Mock()
    client.interactions.create.return_value.output_text = gemini_story_response()
    provider = GeminiStoryProvider("test-gemini-key", client=client)

    provider.generate_story(
        "Video clip: gaming.mp4, duration: 30s, portrait",
        target_duration=40.0,
        constraints={
            "niche": "gaming",
            "creative_angle": "risk versus reward",
            "target_word_count": 100,
            "avoid_recent_stories": [
                {"title": "Old title", "hook": "Old hook"}
            ],
        },
    )

    prompt = client.interactions.create.call_args.kwargs["input"]
    assert "selected topic is 'gaming'" in prompt
    assert "weak topic context" not in prompt
    assert "risk versus reward" in prompt
    assert "100 spoken words" in prompt
    assert "Old title" in prompt
    assert "Old hook" in prompt


def test_gemini_provider_api_failure_does_not_expose_key():
    api_key = "test-secret-gemini-key"
    client = Mock()
    client.interactions.create.side_effect = RuntimeError(
        f"request failed with key={api_key}"
    )
    provider = GeminiStoryProvider(api_key, client=client)

    with pytest.raises(RuntimeError, match="Gemini story generation failed") as error:
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")

    assert api_key not in str(error.value)


def test_gemini_timeout_fails_with_clear_safe_message():
    class ReadTimeout(Exception):
        pass

    client = Mock()
    client.interactions.create.side_effect = ReadTimeout("internal transport details")
    provider = GeminiStoryProvider("test-key", client=client)

    with pytest.raises(RuntimeError, match="timed out after 90 seconds") as error:
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")

    assert "internal transport details" not in str(error.value)


def test_gemini_api_error_exposes_only_safe_server_details():
    from google.genai.errors import ClientError

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

    with pytest.raises(RuntimeError) as error:
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")

    assert str(error.value) == (
        "Gemini API error: HTTP 404 NOT_FOUND: "
        "Requested resource unavailable ([REDACTED])"
    )
    assert api_key not in str(error.value)


@pytest.mark.parametrize("response_text", ["not json", "[]", '{"title": "missing fields"}'])
def test_gemini_provider_rejects_malformed_response(response_text):
    client = Mock()
    client.interactions.create.return_value.output_text = response_text
    provider = GeminiStoryProvider("test-key", client=client)

    with pytest.raises(RuntimeError, match="Gemini returned malformed story data"):
        provider.generate_story("Video clip: sample.mp4, duration: 20s, landscape")
