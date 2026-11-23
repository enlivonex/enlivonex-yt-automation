"""Caption generation strategies."""

import random
from abc import ABC, abstractmethod
from typing import Optional


class CaptionGenerator(ABC):
    """Abstract base class for caption generators."""

    @abstractmethod
    def generate(self, filename: Optional[str] = None) -> str:
        """Generate a caption.

        Args:
            filename: Optional filename for context

        Returns:
            Generated caption text
        """
        pass


class TemplateCaptionGenerator(CaptionGenerator):
    """Template-based caption generator with various humor styles."""

    def __init__(self, style: str = "dark_humor", max_length: int = 100):
        """Initialize template generator.

        Args:
            style: Caption style (dark_humor, funny, sarcastic, wholesome)
            max_length: Maximum caption length
        """
        self.style = style
        self.max_length = max_length
        self._used_captions = set()

        # Caption templates by style
        self.templates = {
            "dark_humor": [
                "When life gives you lemons, throw them back",
                "Another day, another existential crisis",
                "This is fine",
                "Mood: perpetually tired",
                "POV: You've made a terrible mistake",
                "Instructions unclear, got lost in the void",
                "Not sure what I expected but it wasn't this",
                "The universe really said 'no' today",
                "Well that escalated quickly",
                "Chaos is a ladder and I fell off",
                "Living my best life (terms and conditions apply)",
                "Reality hits different at 3am",
                "Error 404: Motivation not found",
                "So we're doing this now",
                "I have questions and concerns",
                "This wasn't in the manual",
                "Expectations vs Reality be like",
                "Nobody warned me about this part",
                "Why is this so relatable though",
                "The plot thickens",
            ],
            "funny": [
                "When you finally understand the assignment",
                "Me trying to adult",
                "This is the content we signed up for",
                "Big mood energy",
                "Living rent free in my head",
                "No thoughts, just vibes",
                "Caught in 4K",
                "The way I screamed",
                "Not me laughing at this",
                "I can't unsee this now",
                "This is why we can't have nice things",
                "Absolutely unhinged",
                "The chaos I needed today",
                "Main character energy",
                "Tell me why this is so accurate",
                "I felt that in my soul",
                "The audacity",
                "Straight up iconic behavior",
                "This speaks to me on a spiritual level",
                "I'm obsessed with this",
            ],
            "sarcastic": [
                "Oh wow, never seen that before",
                "Shocking, truly shocking",
                "What a plot twist",
                "Didn't see that coming",
                "How original",
                "Fascinating stuff right here",
                "Groundbreaking content",
                "Revolutionary discovery",
                "What a time to be alive",
                "The future is now",
                "Thanks, I hate it",
                "Well that's just perfect",
                "Exactly what I needed to see today",
                "Outstanding move",
                "Flawless execution",
                "That's one way to do it",
                "Interesting choice",
                "Bold strategy",
                "This will end well",
                "Nothing could go wrong",
            ],
            "wholesome": [
                "This made my day",
                "Pure happiness",
                "We love to see it",
                "This is beautiful",
                "Wholesome content only",
                "Faith in humanity restored",
                "The goodness we needed",
                "This sparks joy",
                "Living the dream",
                "Positive vibes only",
                "This is the way",
                "Blessed content",
                "Heart = full",
                "The smile this gave me",
                "Good energy",
                "This is what it's all about",
                "Simply wonderful",
                "Pure gold",
                "Treasure this moment",
                "Perfection achieved",
            ],
        }

    def generate(self, filename: Optional[str] = None) -> str:
        """Generate a caption from templates.

        Args:
            filename: Optional filename (currently unused but available for future enhancements)

        Returns:
            Generated caption text
        """
        # Get templates for the selected style
        style_templates = self.templates.get(self.style, self.templates["dark_humor"])

        # Find unused captions first
        available = [c for c in style_templates if c not in self._used_captions]

        # If all captions used, reset the used set
        if not available:
            self._used_captions.clear()
            available = style_templates

        # Select random caption
        caption = random.choice(available)
        self._used_captions.add(caption)

        # Ensure caption fits max length
        if len(caption) > self.max_length:
            caption = caption[:self.max_length - 3] + "..."

        return caption

    def reset_used_captions(self):
        """Reset the tracking of used captions."""
        self._used_captions.clear()


class LLMCaptionGenerator(CaptionGenerator):
    """Placeholder for future LLM-based caption generation."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo"):
        """Initialize LLM generator.

        Args:
            api_key: API key for LLM service
            model: Model to use for generation
        """
        self.api_key = api_key
        self.model = model

    def generate(self, filename: Optional[str] = None) -> str:
        """Generate caption using LLM (not implemented)."""
        raise NotImplementedError(
            "LLM caption generation not yet implemented. "
            "Use TemplateCaptionGenerator for now."
        )
