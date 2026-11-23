"""Tests for YouTube metadata generation."""

import pytest
from app.metadata.models import YouTubeMetadata
from app.metadata.generator import TemplateMetadataGenerator
from app.story.models import Story, StorySegment


@pytest.fixture
def sample_story():
    """Create sample story."""
    segments = [
        StorySegment("Hook line.", 0.0, 3.0, "excited"),
        StorySegment("Main content here.", 3.0, 55.0, None),
        StorySegment("Outro line.", 55.0, 60.0, "friendly")
    ]

    return Story(
        title="Amazing Discovery",
        hook="Hook line.",
        segments=segments,
        total_duration=60.0,
        tone="educational",
        target_audience="general"
    )


def test_metadata_creation():
    """Test creating YouTube metadata."""
    metadata = YouTubeMetadata(
        title="Test Video",
        description="Test description",
        hashtags=["shorts", "test"],
        visibility="private"
    )

    assert metadata.title == "Test Video"
    assert len(metadata.hashtags) == 2
    assert metadata.visibility == "private"


def test_metadata_format_description():
    """Test description formatting with hashtags."""
    metadata = YouTubeMetadata(
        title="Test",
        description="Description text",
        hashtags=["tag1", "tag2", "tag3"]
    )

    formatted = metadata.format_description()

    assert "Description text" in formatted
    assert "#tag1" in formatted
    assert "#tag2" in formatted
    assert "#tag3" in formatted


def test_metadata_to_dict():
    """Test converting metadata to YouTube API format."""
    metadata = YouTubeMetadata(
        title="Test Video",
        description="Test description",
        hashtags=["shorts"],
        category="22",
        visibility="public",
        made_for_kids=False
    )

    data = metadata.to_dict()

    assert data["snippet"]["title"] == "Test Video"
    assert data["snippet"]["categoryId"] == "22"
    assert data["status"]["privacyStatus"] == "public"
    assert data["status"]["selfDeclaredMadeForKids"] is False


def test_metadata_title_truncation():
    """Test title truncation at 100 chars."""
    long_title = "A" * 150

    metadata = YouTubeMetadata(
        title=long_title,
        description="Test",
        hashtags=[]
    )

    data = metadata.to_dict()
    assert len(data["snippet"]["title"]) == 100


def test_template_generator(sample_story):
    """Test template-based metadata generation."""
    generator = TemplateMetadataGenerator()

    metadata = generator.generate_metadata(sample_story, keywords=["science"])

    assert metadata.title == "Amazing Discovery"
    assert "Hook line" in metadata.description
    assert len(metadata.hashtags) > 0
    assert "science" in metadata.hashtags


def test_template_generator_tone_hashtags(sample_story):
    """Test tone-specific hashtag generation."""
    generator = TemplateMetadataGenerator()

    metadata = generator.generate_metadata(sample_story)

    # Educational tone should add educational hashtags
    assert "educational" in metadata.hashtags or "learnontiktok" in metadata.hashtags


def test_template_generator_no_keywords(sample_story):
    """Test metadata generation without keywords."""
    generator = TemplateMetadataGenerator()

    metadata = generator.generate_metadata(sample_story)

    assert metadata.title is not None
    assert metadata.description is not None
    assert len(metadata.hashtags) > 0


def test_template_generator_hashtag_deduplication(sample_story):
    """Test hashtag deduplication."""
    generator = TemplateMetadataGenerator()

    # Pass duplicate keywords
    metadata = generator.generate_metadata(
        sample_story,
        keywords=["shorts", "test", "shorts", "test"]
    )

    # Should deduplicate
    hashtag_counts = {}
    for tag in metadata.hashtags:
        hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1

    # No duplicates
    assert all(count == 1 for count in hashtag_counts.values())


def test_template_generator_hashtag_limit(sample_story):
    """Test hashtag limit (max 10)."""
    generator = TemplateMetadataGenerator()

    # Pass many keywords
    many_keywords = [f"keyword{i}" for i in range(20)]

    metadata = generator.generate_metadata(sample_story, keywords=many_keywords)

    # Should limit to 10
    assert len(metadata.hashtags) <= 10
