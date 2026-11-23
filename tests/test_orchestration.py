"""Tests for narration-driven source clip selection."""

from datetime import datetime, timedelta
import json
from unittest.mock import MagicMock, Mock

from app.clips.selector import LeastUsedSelector
from app.database.db import Database
from app.database.models import ArtifactType, ClipStatus, SourceClip
from app.metadata.models import YouTubeMetadata
from app.orchestration.job import JobOrchestrator
from app.research.service import ResearchSource
from app.story.models import Story, StorySegment


def make_clip(clip_id: int, duration: float, times_used: int = 0) -> SourceClip:
    return SourceClip(
        id=clip_id,
        filename=f"clip_{clip_id}.mp4",
        file_hash=f"hash-{clip_id}",
        file_path=f"clip_{clip_id}.mp4",
        duration_seconds=duration,
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        orientation="portrait",
        file_size_bytes=1000,
        fps=30.0,
        codec="h264",
        status=ClipStatus.AVAILABLE,
        times_used=times_used,
        created_at=datetime.now() + timedelta(seconds=clip_id),
        last_used_at=None,
        metadata_json=None,
    )


def make_orchestrator_with_clips(candidates):
    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    orchestrator.db = MagicMock()
    orchestrator.clip_manager = Mock()
    orchestrator.clip_manager.get_available_clips.return_value = candidates
    return orchestrator


def test_adds_distinct_least_used_clips_until_narration_is_covered():
    first = make_clip(1, 8.0)
    more_used = make_clip(2, 8.0, times_used=3)
    least_used = make_clip(3, 9.0, times_used=1)
    orchestrator = make_orchestrator_with_clips([first, more_used, least_used])

    clips = orchestrator._select_clips_for_narration(
        first, LeastUsedSelector(), narration_duration=15.0
    )

    assert [clip.id for clip in clips] == [1, 3]
    orchestrator.clip_manager.get_available_clips.assert_called_once_with(limit=100)


def test_uses_all_distinct_clips_when_library_is_shorter_than_narration():
    first = make_clip(1, 8.0)
    second = make_clip(2, 5.0)
    orchestrator = make_orchestrator_with_clips([first, second])

    clips = orchestrator._select_clips_for_narration(
        first, LeastUsedSelector(), narration_duration=20.0
    )

    assert [clip.id for clip in clips] == [1, 2]


def test_story_narration_is_not_truncated_to_the_requested_duration():
    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    orchestrator.story_provider = Mock()
    long_narration = " ".join(f"word{i}" for i in range(100))
    orchestrator.story_provider.generate_story.return_value = Story(
        title="Story",
        hook="Opening",
        segments=[StorySegment(long_narration, 0.0, 60.0)],
        total_duration=60.0,
        tone="casual",
    )
    orchestrator._get_recent_stories = lambda niche=None: []

    orchestrator._generate_story(
        make_clip(1, 8.0), target_duration=35.0, niche="fitness"
    )

    generated_story = orchestrator.story_provider.generate_story.return_value
    assert len(generated_story.full_text.split()) == 100
    request = orchestrator.story_provider.generate_story.call_args.kwargs
    assert request["target_duration"] == 35.0
    assert request["constraints"]["niche"] == "fitness"
    assert "gaming.mp4" not in request["clip_description"]
    assert "unrelated b-roll" in request["clip_description"]


def test_story_generation_receives_selected_target_and_recent_story_context(monkeypatch):
    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    orchestrator.story_provider = Mock()
    orchestrator.story_provider.generate_story.return_value = Story(
        title="A New Angle",
        hook="Think twice before taking the risk.",
        segments=[StorySegment("Narration", 0.0, 35.0)],
        total_duration=35.0,
        tone="casual",
    )
    recent_stories = [{"title": "Previous", "hook": "A familiar opening"}]
    monkeypatch.setattr(
        orchestrator, "_get_recent_stories", lambda niche=None: recent_stories
    )
    monkeypatch.setattr(
        "app.orchestration.job.random.choice",
        lambda angles: angles[0],
    )

    orchestrator._generate_story(
        make_clip(1, 8.0),
        target_duration=35.0,
        niche="health",
    )

    request = orchestrator.story_provider.generate_story.call_args.kwargs
    assert request["target_duration"] == 35.0
    assert request["constraints"]["niche"] == "health"
    assert request["constraints"]["target_word_count"] == 88
    assert request["constraints"]["avoid_recent_stories"] == recent_stories


def test_recent_story_context_only_includes_matching_topic(tmp_path):
    db = Database(tmp_path / "media.db")
    with db:
        db.initialize()
        for job_id, niche, title in (
            ("JOB-HEALTH", "health", "Earlier health title"),
            ("JOB-GAMING", "gaming", "Earlier gaming title"),
        ):
            db.conn.execute(
                """
                INSERT INTO jobs (
                    job_id, source_clip_id, status, current_stage, started_at,
                    completed_at, error_message, config_snapshot
                ) VALUES (?, NULL, 'COMPLETED', 'COMPLETED', ?, NULL, NULL, ?)
                """,
                (
                    job_id,
                    datetime.now().isoformat(),
                    json.dumps({"story_niche": niche}),
                ),
            )
            db.conn.execute(
                """
                INSERT INTO job_artifacts (
                    job_id, artifact_type, content_text, file_path, provider,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, NULL, NULL, NULL, ?)
                """,
                (
                    job_id,
                    "STORY",
                    json.dumps({"title": title, "hook": "Earlier hook"}),
                    datetime.now().isoformat(),
                ),
            )
        db.conn.commit()

    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    orchestrator.db = db

    assert orchestrator._get_recent_stories(niche="health") == [
        {"title": "Earlier health title", "hook": "Earlier hook"}
    ]


def test_metadata_contains_citations_and_uses_selected_topic_keyword():
    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    metadata_generator = Mock()
    metadata_generator.generate_metadata.return_value = YouTubeMetadata(
        title="Health tips",
        description="Evidence-led description.",
        hashtags=["shorts"],
    )
    orchestrator.metadata_generator = metadata_generator
    story = Story(
        title="Health tips",
        hook="A health hook",
        segments=[StorySegment("Health content.", 0, 1)],
        total_duration=1,
        tone="educational",
    )
    sources = [
        ResearchSource("Medical source", "https://example.org/health", "web")
    ]

    metadata = orchestrator._generate_metadata(story, "health", sources)

    metadata_generator.generate_metadata.assert_called_once_with(
        story, keywords=["health"]
    )
    assert "Sources:" in metadata.description
    assert "https://example.org/health" in metadata.description


def test_saved_content_artifacts_are_written_to_their_paths(tmp_path):
    orchestrator = JobOrchestrator.__new__(JobOrchestrator)
    orchestrator.db = Database(tmp_path / "artifacts.db")
    output = tmp_path / "job" / "research.json"
    with orchestrator.db:
        orchestrator.db.initialize()
    job_id = orchestrator.create_job(config={})

    orchestrator._save_artifact(
        job_id,
        ArtifactType.RESEARCH,
        {"topic": "health"},
        output,
    )

    assert output.read_text(encoding="utf-8") == '{\n  "topic": "health"\n}'
