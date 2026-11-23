"""YouTube metadata models."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class YouTubeMetadata:
    """Complete YouTube video metadata."""
    title: str
    description: str
    hashtags: List[str]
    category: str = "22"  # People & Blogs
    visibility: str = "private"  # private, unlisted, public
    made_for_kids: bool = False

    def format_description(self) -> str:
        """Format description with hashtags.

        Returns:
            Description with hashtags appended
        """
        lines = [self.description, ""]

        # Add hashtags
        if self.hashtags:
            hashtag_line = " ".join(f"#{tag}" for tag in self.hashtags)
            lines.append(hashtag_line)

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to dictionary for YouTube API.

        Returns:
            Dictionary representation
        """
        return {
            "snippet": {
                "title": self.title[:100],  # YouTube title limit
                "description": self.format_description()[:5000],  # Description limit
                "categoryId": self.category,
            },
            "status": {
                "privacyStatus": self.visibility,
                "selfDeclaredMadeForKids": self.made_for_kids
            }
        }
