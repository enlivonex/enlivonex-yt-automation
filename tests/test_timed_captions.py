"""Tests for timed captions."""

import pytest
from pathlib import Path
from app.captions.timed import (
    CaptionWord, CaptionSegment, CaptionStyle, TimedCaptions
)


def test_caption_word_creation():
    """Test creating a caption word."""
    word = CaptionWord(word="Hello", start_time=0.0, end_time=0.5)

    assert word.word == "Hello"
    assert word.start_time == 0.0
    assert word.end_time == 0.5


def test_caption_segment_creation():
    """Test creating a caption segment."""
    words = [
        CaptionWord("Hello", 0.0, 0.5),
        CaptionWord("world", 0.5, 1.0)
    ]

    segment = CaptionSegment(
        words=words,
        text="Hello world",
        start_time=0.0,
        end_time=1.0
    )

    assert len(segment.words) == 2
    assert segment.text == "Hello world"
    assert segment.duration == 1.0


def test_caption_style_defaults():
    """Test default caption style."""
    style = CaptionStyle()

    assert style.font_family == "Arial"
    assert style.font_size == 24
    assert style.font_color == "#FFFFFF"
    assert style.outline_color == "#000000"
    assert style.uppercase is True


def test_timed_captions_creation():
    """Test creating timed captions."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 0.0, 1.0)],
            text="Test",
            start_time=0.0,
            end_time=1.0
        ),
        CaptionSegment(
            words=[CaptionWord("caption", 1.0, 2.0)],
            text="caption",
            start_time=1.0,
            end_time=2.0
        )
    ]

    captions = TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=2.0
    )

    assert len(captions.segments) == 2
    assert captions.total_duration == 2.0


def test_srt_export():
    """Test SRT format export."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Hello", 0.0, 1.0)],
            text="Hello",
            start_time=0.0,
            end_time=1.0
        ),
        CaptionSegment(
            words=[CaptionWord("world", 1.0, 2.0)],
            text="world",
            start_time=1.0,
            end_time=2.0
        )
    ]

    captions = TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=2.0
    )

    srt = captions.to_srt()

    assert "1" in srt
    assert "00:00:00,000 --> 00:00:01,000" in srt
    assert "Hello" in srt
    assert "2" in srt
    assert "world" in srt


def test_vtt_export():
    """Test WebVTT format export."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 0.0, 1.0)],
            text="Test",
            start_time=0.0,
            end_time=1.0
        )
    ]

    captions = TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=1.0
    )

    vtt = captions.to_vtt()

    assert vtt.startswith("WEBVTT")
    assert "00:00:00,000 --> 00:00:01,000" in vtt
    assert "Test" in vtt


def test_ass_export():
    """Test ASS format export."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 0.0, 1.0)],
            text="Test",
            start_time=0.0,
            end_time=1.0
        )
    ]

    style = CaptionStyle(
        font_family="Arial",
        font_size=24,
        uppercase=True
    )

    captions = TimedCaptions(
        segments=segments,
        style=style,
        total_duration=1.0
    )

    ass = captions.to_ass()

    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass
    assert "TEST" in ass  # uppercase


def test_save_srt_file(tmp_path):
    """Test saving SRT file."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 0.0, 1.0)],
            text="Test",
            start_time=0.0,
            end_time=1.0
        )
    ]

    captions = TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=1.0
    )

    srt_path = tmp_path / "test.srt"
    captions.save_srt(srt_path)

    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8")
    assert "Test" in content


def test_timestamp_formatting():
    """Test timestamp formatting."""
    segments = [
        CaptionSegment(
            words=[CaptionWord("Test", 65.5, 66.5)],
            text="Test",
            start_time=65.5,
            end_time=66.5
        )
    ]

    captions = TimedCaptions(
        segments=segments,
        style=CaptionStyle(),
        total_duration=70.0
    )

    srt = captions.to_srt()

    # Should be 00:01:05,500 (1 minute, 5.5 seconds)
    assert "00:01:05,500" in srt
    assert "00:01:06,500" in srt


def test_color_conversion():
    """Test hex color to ASS format conversion."""
    captions = TimedCaptions(
        segments=[],
        style=CaptionStyle(),
        total_duration=0.0
    )

    # White
    assert captions._color_to_ass("#FFFFFF") == "&H00FFFFFF"

    # Black
    assert captions._color_to_ass("#000000") == "&H00000000"

    # Custom color
    result = captions._color_to_ass("#FF0000")
    assert "&H" in result
