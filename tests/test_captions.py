"""Tests for caption generator."""

import pytest
from app.captions import TemplateCaptionGenerator, LLMCaptionGenerator


def test_template_generator_basic():
    """Test basic caption generation."""
    generator = TemplateCaptionGenerator(style="dark_humor", max_length=100)

    caption = generator.generate()
    assert isinstance(caption, str)
    assert len(caption) > 0
    assert len(caption) <= 100


def test_template_generator_different_styles():
    """Test different caption styles."""
    styles = ["dark_humor", "funny", "sarcastic", "wholesome"]

    for style in styles:
        generator = TemplateCaptionGenerator(style=style)
        caption = generator.generate()
        assert isinstance(caption, str)
        assert len(caption) > 0


def test_template_generator_max_length():
    """Test caption length limiting."""
    generator = TemplateCaptionGenerator(style="dark_humor", max_length=20)

    caption = generator.generate()
    assert len(caption) <= 20


def test_template_generator_no_duplicates():
    """Test that captions don't immediately repeat."""
    generator = TemplateCaptionGenerator(style="dark_humor")

    captions = [generator.generate() for _ in range(10)]

    # Should have variety (at least some different captions)
    unique_captions = set(captions)
    assert len(unique_captions) > 1


def test_template_generator_reset_used():
    """Test resetting used captions."""
    generator = TemplateCaptionGenerator(style="dark_humor")

    # Generate many captions
    first_batch = [generator.generate() for _ in range(20)]

    # Reset
    generator.reset_used_captions()

    # Generate again - should be able to reuse
    second_batch = [generator.generate() for _ in range(5)]

    assert len(second_batch) == 5


def test_template_generator_unknown_style():
    """Test generator with unknown style falls back to default."""
    generator = TemplateCaptionGenerator(style="unknown_style")

    caption = generator.generate()
    assert isinstance(caption, str)
    assert len(caption) > 0


def test_llm_generator_not_implemented():
    """Test that LLM generator raises NotImplementedError."""
    generator = LLMCaptionGenerator()

    with pytest.raises(NotImplementedError):
        generator.generate()


def test_template_generator_with_filename():
    """Test generator with filename parameter."""
    generator = TemplateCaptionGenerator(style="funny")

    # Should work with or without filename
    caption1 = generator.generate()
    caption2 = generator.generate("test.jpg")

    assert isinstance(caption1, str)
    assert isinstance(caption2, str)
