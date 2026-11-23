"""Metadata generation providers."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, List

from app.ai.ollama import JSONGenerator, OllamaClient
from .models import YouTubeMetadata
from ..story.models import Story

logger = logging.getLogger(__name__)


class MetadataGenerator(ABC):
    """Base class for metadata generation."""

    @abstractmethod
    def generate_metadata(
        self,
        story: Story,
        keywords: Optional[List[str]] = None
    ) -> YouTubeMetadata:
        """Generate YouTube metadata from story.

        Args:
            story: Story object
            keywords: Optional list of keywords/topics

        Returns:
            YouTubeMetadata object
        """
        pass


class TemplateMetadataGenerator(MetadataGenerator):
    """Simple template-based metadata generation."""

    def generate_metadata(
        self,
        story: Story,
        keywords: Optional[List[str]] = None
    ) -> YouTubeMetadata:
        """Generate metadata using templates."""
        keywords = keywords or []

        # Use story title
        title = story.title[:100]

        # Create description from story
        description = f"{story.hook}\n\n{story.full_text[:200]}..."

        # Generate hashtags from keywords and tone
        hashtags = []
        if keywords:
            hashtags.extend(keywords[:5])

        # Add tone-based hashtags
        tone_hashtags = {
            "casual": ["shorts", "viral", "trending"],
            "educational": ["shorts", "educational", "learnontiktok"],
            "dramatic": ["shorts", "storytime", "viral"],
            "peaceful": ["shorts", "relaxing", "mindful"]
        }

        if story.tone in tone_hashtags:
            hashtags.extend(tone_hashtags[story.tone])

        # Deduplicate and limit
        hashtags = list(dict.fromkeys(hashtags))[:10]

        return YouTubeMetadata(
            title=title,
            description=description,
            hashtags=hashtags,
            visibility="private"
        )


class OllamaMetadataGenerator(MetadataGenerator):
    """Generate video metadata with a local Ollama model."""

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300,
        client: Optional[JSONGenerator] = None,
    ):
        self.client = client or OllamaClient(base_url, model, timeout_seconds)

    def unload(self) -> None:
        """Unload the local model after standalone metadata generation."""
        unload = getattr(self.client, "unload", None)
        if unload:
            unload()

    def generate_metadata(
        self,
        story: Story,
        keywords: Optional[List[str]] = None,
    ) -> YouTubeMetadata:
        data = self.client.generate_json(
            "Generate concise, accurate YouTube video metadata about the user's "
            "selected topic in Keywords. Do not switch to a different topic. "
            "Return only a JSON "
            "object with string title, string description, and an array of "
            "hashtag strings. Do not invent facts beyond the supplied story.",
            f"""Create YouTube Shorts metadata for this story.

Title: {story.title}
Hook: {story.hook}
Tone: {story.tone}
Keywords: {', '.join(keywords or [])}
Story:
{story.full_text}

Use a title of at most 100 characters, a short accurate description, and up to
10 relevant hashtags without the # prefix.""",
        )
        used_story_defaults = []
        title = data.get("title") or data.get("video_title")
        if not title:
            title = story.title
            used_story_defaults.append("title")
        description = data.get("description") or data.get("video_description")
        if not description:
            description = story.hook or story.full_text[:200]
            used_story_defaults.append("description")
        hashtags = data.get("hashtags", data.get("tags", []))
        if isinstance(hashtags, str):
            hashtags = [
                tag.strip().lstrip("#")
                for tag in hashtags.replace(",", " ").split()
            ]
        if hashtags is None:
            hashtags = []
        if not hashtags:
            hashtags = list(keywords or [])
            hashtags.extend(("shorts", story.tone))
            used_story_defaults.append("hashtags")

        try:
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(description, str)
                or not isinstance(hashtags, list)
                or any(not isinstance(tag, str) for tag in hashtags)
            ):
                raise ValueError("Metadata fields have invalid types")
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                f"Ollama returned malformed metadata ({type(error).__name__})."
            ) from error

        if used_story_defaults:
            logger.warning(
                "Ollama metadata omitted %s; using story-derived defaults.",
                ", ".join(used_story_defaults),
            )

        return YouTubeMetadata(
            title=title.strip()[:100],
            description=description.strip(),
            hashtags=list(dict.fromkeys(
                tag.strip().lstrip("#").replace(" ", "")
                for tag in hashtags
                if tag.strip().lstrip("#")
            ))[:10],
            visibility="private",
        )


class LLMMetadataGenerator(MetadataGenerator):
    """Cloud LLM-based metadata generation."""

    def __init__(self, provider: str, api_key: str, model: Optional[str] = None):
        """Initialize LLM metadata generator.

        Args:
            provider: "openai", "anthropic", or "gemini"
            api_key: API key
            model: Model to use (provider-specific defaults)
        """
        self.provider = provider.lower()
        self.api_key = api_key

        if self.provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
            self.model = model or "gpt-4o"
        elif self.provider == "anthropic":
            from anthropic import Anthropic
            self.client = Anthropic(api_key=api_key)
            self.model = model or "claude-sonnet-4-20250514"
        elif self.provider == "gemini":
            from google import genai
            self.client = genai.Client(api_key=api_key)
            self.model = model or "gemini-3.8-flash"
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def generate_metadata(
        self,
        story: Story,
        keywords: Optional[List[str]] = None
    ) -> YouTubeMetadata:
        """Generate metadata using LLM."""
        keywords = keywords or []

        prompt = f"""Create YouTube Shorts metadata for this story. The keywords
are the user's selected topic and must guide the title, description, and hashtags.
Do not substitute another topic.

Title: {story.title}
Hook: {story.hook}
Tone: {story.tone}
Keywords: {', '.join(keywords)}

Story:
{story.full_text}

Generate:
1. A catchy title (max 100 chars)
2. An engaging description (2-3 sentences)
3. 5-10 relevant hashtags

Return ONLY valid JSON:
{{
  "title": "...",
  "description": "...",
  "hashtags": ["tag1", "tag2", ...]
}}"""

        if self.provider == "openai":
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.7
            )
            content = response.choices[0].message.content
        elif self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            content = response.content[0].text

            # Extract JSON from markdown if needed
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
        else:
            response = self.client.interactions.create(
                model=self.model,
                input=prompt,
                timeout=90,
                response_format={"type": "text", "mime_type": "application/json"},
            )
            content = getattr(response, "output_text", None)

        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"{self.provider} returned empty metadata.")
        data = json.loads(content)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("title"), str)
            or not isinstance(data.get("description"), str)
            or not isinstance(data.get("hashtags"), list)
            or any(not isinstance(tag, str) for tag in data["hashtags"])
        ):
            raise RuntimeError(
                f"{self.provider} returned malformed metadata fields."
            )

        return YouTubeMetadata(
            title=data["title"][:100],
            description=data["description"],
            hashtags=data["hashtags"][:10],
            visibility="private"
        )


def create_metadata_generator(
    settings, ollama_client: Optional[JSONGenerator] = None
) -> MetadataGenerator:
    """Build metadata generation from the selected story provider."""
    provider = settings.story_provider
    if provider == "ollama":
        return OllamaMetadataGenerator(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.ollama_timeout_seconds,
            client=ollama_client,
        )
    if provider in {"openai", "anthropic", "gemini"}:
        api_key = getattr(settings, f"{provider}_api_key")
        model = getattr(settings, f"{provider}_story_model")
        if not api_key.strip():
            raise ValueError(
                f"{provider} metadata generation requires its configured API key."
            )
        return LLMMetadataGenerator(provider, api_key=api_key, model=model)
    if provider in {"mock", "template"}:
        return TemplateMetadataGenerator()
    raise ValueError(f"Unsupported metadata provider: {provider}")
