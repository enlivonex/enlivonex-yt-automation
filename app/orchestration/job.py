"""Job orchestration system for Enlivonex YouTube Automation."""

import json
import math
import random
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from ..database.db import Database
from ..database.models import Job, JobStatus, JobArtifact, ArtifactType, SourceClip
from ..clips.manager import ClipManager
from ..clips.selector import ClipSelector, LeastUsedSelector, ClipSelectionError
from ..story.provider import StoryProvider
from ..story.models import Story
from ..voiceover.provider import VoiceProvider
from ..voiceover.models import VoiceConfig, VoiceSegment
from ..captions.timed_generator import TimedCaptionGenerator
from ..captions.timed import CaptionStyle
from ..composition.composer import ShortsComposer, CompositionConfig
from ..metadata.generator import MetadataGenerator
from ..research import ResearchService, ResearchSource


VIDEO_DURATION_TOLERANCE_SECONDS = 3.5


class JobOrchestrator:
    """Orchestrates complete YouTube Shorts generation job."""

    def __init__(
        self,
        db: Database,
        clip_manager: ClipManager,
        story_provider: StoryProvider,
        voice_provider: VoiceProvider,
        metadata_generator: MetadataGenerator,
        work_dir: Path,
        research_service: Optional[ResearchService] = None,
    ):
        """Initialize job orchestrator.

        Args:
            db: Database instance
            clip_manager: Clip library manager
            story_provider: Story generation provider
            voice_provider: Voiceover provider
            metadata_generator: Metadata generator
            work_dir: Working directory for job artifacts
        """
        self.db = db
        self.clip_manager = clip_manager
        self.story_provider = story_provider
        self.voice_provider = voice_provider
        self.metadata_generator = metadata_generator
        self.work_dir = work_dir
        self.research_service = research_service

        self.caption_generator = TimedCaptionGenerator(style=CaptionStyle())
        self.composer = ShortsComposer()

    def create_job(
        self,
        clip_selector: Optional[ClipSelector] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create new job.

        Args:
            clip_selector: Clip selection strategy (defaults to LeastUsedSelector)
            config: Job configuration

        Returns:
            Job ID
        """
        clip_selector = clip_selector or LeastUsedSelector()
        config = config or {}

        # Generate job ID
        timestamp = datetime.now().strftime("%Y%m%d")
        random_suffix = str(uuid.uuid4())[:6].upper()
        job_id = f"JOB-{timestamp}-{random_suffix}"

        # Create job record
        job = Job(
            id=None,
            job_id=job_id,
            source_clip_id=None,
            status=JobStatus.QUEUED,
            current_stage="QUEUED",
            started_at=datetime.now(),
            completed_at=None,
            error_message=None,
            config_snapshot=json.dumps(config)
        )

        with self.db:
            job.id = self.db.insert_job(job)

        return job_id

    def run_job(
        self,
        job_id: str,
        clip_selector: Optional[ClipSelector] = None,
        voice_config: Optional[VoiceConfig] = None,
        composition_config: Optional[CompositionConfig] = None,
        dry_run: bool = False,
        story_duration: float = 30.0,
        story_niche: str = "",
    ) -> Dict[str, Any]:
        """Execute complete job pipeline.

        Args:
            job_id: Job ID
            clip_selector: Clip selection strategy
            voice_config: Voice configuration
            composition_config: Composition configuration
            dry_run: If True, skip actual processing

        Returns:
            Job result summary
        """
        clip_selector = clip_selector or LeastUsedSelector()
        voice_config = voice_config or VoiceConfig(provider="mock", voice_id="default")
        composition_config = composition_config or CompositionConfig()
        if (
            not math.isfinite(story_duration)
            or story_duration < 20
            or story_duration > 90
        ):
            raise ValueError(
                "Story duration must be between 20 and 90 seconds."
            )
        if not story_niche.strip():
            raise ValueError("Story niche cannot be empty.")
        target_story_duration = story_duration
        composition_config.target_duration = None

        # Create job directory
        job_dir = self.work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        narration_duration = None
        try:
            # Stage 1: Select clip
            with self.db:
                self.db.update_job_status(job_id, JobStatus.SELECTING_CLIP, "SELECTING_CLIP")

            clip = self._select_clip(clip_selector)
            selected_clips = [clip]

            with self.db:
                self.db.conn.cursor().execute(
                    "UPDATE jobs SET source_clip_id = ? WHERE job_id = ?",
                    (clip.id, job_id)
                )
                self.db.conn.commit()

            # Stage 2: Research the selected topic and retrieve local knowledge.
            if self.research_service is None:
                raise RuntimeError("Topic research is not configured.")
            with self.db:
                self.db.update_job_status(
                    job_id, JobStatus.RESEARCHING, "RESEARCHING"
                )
            research = self.research_service.research(story_niche)
            self._save_artifact(
                job_id,
                ArtifactType.RESEARCH,
                research.to_dict(),
                job_dir / "research.json",
            )

            # Stage 3: Generate story
            with self.db:
                self.db.update_job_status(job_id, JobStatus.GENERATING_STORY, "GENERATING_STORY")

            story = self._generate_story(
                clip,
                target_duration=target_story_duration,
                niche=story_niche,
                words_per_second=(
                    2.75 if voice_config.provider == "offline" else 2.5
                ),
                research_context=research.context,
                research_sources=[
                    {
                        "title": source.title,
                        "url": source.url,
                        "source_type": source.source_type,
                    }
                    for source in research.sources
                ],
            )
            self._save_artifact(job_id, ArtifactType.STORY, story.to_dict(), job_dir / "story.json")

            # Stage 3: Generate voiceover
            if not dry_run:
                with self.db:
                    self.db.update_job_status(job_id, JobStatus.GENERATING_VOICE, "GENERATING_VOICE")

                voice_segments = self._generate_voiceover(story, voice_config, job_dir)
                narration_duration = sum(
                    segment.duration for segment in voice_segments
                )
                selected_clips = self._select_clips_for_narration(
                    clip, clip_selector, narration_duration
                )
                self._save_artifact(
                    job_id,
                    ArtifactType.VOICEOVER,
                    {"segments": [str(s.audio_path) for s in voice_segments]},
                    None
                )

                # Stage 4: Generate captions
                with self.db:
                    self.db.update_job_status(job_id, JobStatus.GENERATING_CAPTIONS, "GENERATING_CAPTIONS")

                captions = self._generate_captions(
                    voice_segments, job_dir
                )

                # Stage 5: Compose video
                with self.db:
                    self.db.update_job_status(job_id, JobStatus.RENDERING, "RENDERING")

                video_path = self._compose_video(
                    selected_clips, voice_segments, captions, composition_config, job_dir
                )
                self._save_artifact(job_id, ArtifactType.VIDEO, None, video_path)

                # Stage 6: Generate metadata
                with self.db:
                    self.db.update_job_status(job_id, JobStatus.GENERATING_METADATA, "GENERATING_METADATA")

                metadata = self._generate_metadata(
                    story, story_niche, research.sources
                )
                self._save_artifact(job_id, ArtifactType.METADATA, metadata.to_dict(), job_dir / "metadata.json")

            # Mark complete
            with self.db:
                if not dry_run:
                    for used_clip in selected_clips:
                        if used_clip.id is not None:
                            self.clip_manager.mark_clip_used(used_clip.id)
                self.db.update_job_status(job_id, JobStatus.COMPLETED, "COMPLETED")

            return {
                "job_id": job_id,
                "status": "completed",
                "clip": clip.filename,
                "clips": [item.filename for item in selected_clips],
                "story_title": story.title,
                "target_story_duration": target_story_duration,
                "narration_duration": narration_duration,
                "duration_within_tolerance": (
                    narration_duration is None
                    or abs(narration_duration - target_story_duration)
                    <= VIDEO_DURATION_TOLERANCE_SECONDS
                ),
                "research_warning": research.warning,
                "artifacts_dir": str(job_dir)
            }

        except Exception as e:
            with self.db:
                self.db.update_job_status(job_id, JobStatus.FAILED, "FAILED", str(e))
            raise

    def _select_clip(self, selector: ClipSelector) -> SourceClip:
        """Select source clip."""
        with self.db:
            unused_clips = self.clip_manager.get_available_clips(limit=100)

        if not unused_clips:
            raise RuntimeError("No clips available")

        selected = selector.select(unused_clips, count=1)
        if not selected:
            raise RuntimeError("Clip selection failed")

        return selected[0]

    def _select_clips_for_narration(
        self,
        first_clip: SourceClip,
        selector: ClipSelector,
        narration_duration: float,
    ) -> list[SourceClip]:
        """Add distinct least-used clips until the measured narration is covered."""
        clips = [first_clip]
        available_duration = first_clip.duration_seconds
        if available_duration >= narration_duration - 0.5:
            return clips

        with self.db:
            candidates = self.clip_manager.get_available_clips(limit=100)

        candidates = [
            clip for clip in candidates
            if clip.id != first_clip.id and clip.duration_seconds > 0
        ]
        ranked_candidates = selector.select(candidates, count=len(candidates))

        for candidate in ranked_candidates:
            clips.append(candidate)
            available_duration += candidate.duration_seconds
            if available_duration >= narration_duration - 0.5:
                break

        return clips

    def _generate_story(
        self,
        clip: SourceClip,
        target_duration: float = 60.0,
        niche: str = "",
        words_per_second: float = 2.5,
        research_context: str = "",
        research_sources: Optional[list[dict[str, str]]] = None,
    ) -> Story:
        """Generate a topic-led story; the selected clip is unrelated b-roll."""
        topic = niche.strip()
        if not topic:
            raise ValueError("A story topic is required.")
        clip_description = (
            f"The selected background asset is {clip.duration_seconds:.1f}s "
            f"of {clip.orientation} unrelated b-roll. Do not use its visuals "
            "or subject to choose the narration topic."
        )
        recent_stories = self._get_recent_stories(niche=topic)
        angles = (
            "an interesting research-backed fact about the selected topic",
            "a common misconception about the selected topic",
            "how a specific idea within the selected topic works",
            "a practical takeaway directly related to the selected topic",
            "why one research finding about the selected topic matters",
            "a surprising connection within the selected topic",
        )
        story = self.story_provider.generate_story(
            clip_description=clip_description,
            target_duration=target_duration,
            tone="casual",
            constraints={
                "niche": topic,
                "creative_angle": random.choice(angles),
                "target_word_count": round(target_duration * words_per_second),
                "avoid_recent_stories": recent_stories,
                "research_context": research_context,
                "research_sources": research_sources or [],
            },
        )
        if not story.segments or not story.full_text.strip():
            raise RuntimeError("Story provider returned no narration text.")
        return story

    def _get_recent_stories(
        self, limit: int = 8, niche: Optional[str] = None
    ) -> list[dict[str, str]]:
        """Load same-topic story hooks and titles to guide content variety."""
        with self.db:
            rows = self.db.conn.execute(
                """
                SELECT artifacts.content_text, jobs.config_snapshot
                FROM job_artifacts AS artifacts
                JOIN jobs ON jobs.job_id = artifacts.job_id
                WHERE artifacts.artifact_type = ?
                  AND artifacts.content_text IS NOT NULL
                ORDER BY artifacts.created_at DESC
                LIMIT ?
                """,
                (ArtifactType.STORY.value, limit * 5 if niche else limit),
            ).fetchall()

        stories = []
        target_niche = niche.strip().casefold() if niche else None
        for row in rows:
            try:
                data = json.loads(row[0])
                config = json.loads(row[1] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if target_niche:
                saved_niche = str(config.get("story_niche", "")).strip().casefold()
                if saved_niche != target_niche:
                    continue
            if isinstance(data, dict):
                stories.append({
                    "title": str(data.get("title", "")),
                    "hook": str(data.get("hook", "")),
                })
                if len(stories) >= limit:
                    break
        return stories

    def _generate_voiceover(
        self, story: Story, voice_config: VoiceConfig, job_dir: Path
    ) -> list:
        """Generate voiceover audio."""
        audio_dir = job_dir / "audio"
        audio_dir.mkdir(exist_ok=True)

        segments = self.voice_provider.generate_voiceover(story, audio_dir, voice_config)

        return segments

    def _generate_captions(self, voice_segments: list, job_dir: Path):
        """Generate timed captions."""
        captions = self.caption_generator.generate_from_voiceover(
            voice_segments, words_per_caption=3
        )

        captions_file = job_dir / "captions.ass"
        captions.save_ass(captions_file)

        return captions

    def _compose_video(
        self, clips: list[SourceClip], voice_segments: list, captions,
        config: CompositionConfig, job_dir: Path
    ) -> Path:
        """Compose final video."""
        output_path = job_dir / "output.mp4"

        self.composer.compose(
            source_clip=(
                [Path(clip.file_path) for clip in clips]
                if len(clips) > 1 else Path(clips[0].file_path)
            ),
            voiceover_segments=voice_segments,
            captions=captions,
            output_path=output_path,
            config=config
        )

        return output_path

    def _generate_metadata(
        self, story: Story, topic: str, sources: list[ResearchSource]
    ):
        """Generate YouTube metadata."""
        metadata = self.metadata_generator.generate_metadata(
            story, keywords=[topic]
        )
        citations = []
        seen_urls = set()
        for source in sources:
            if source.url in seen_urls:
                continue
            seen_urls.add(source.url)
            citations.append(f"- {source.title}: {source.url}")
        if citations:
            metadata.description = (
                metadata.description.rstrip()
                + "\n\nSources:\n"
                + "\n".join(citations)
            )
        return metadata

    def _save_artifact(
        self, job_id: str, artifact_type: ArtifactType, content: Optional[dict], file_path: Optional[Path]
    ):
        """Save job artifact to database."""
        if content is not None and file_path is not None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps(content, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        artifact = JobArtifact(
            id=None,
            job_id=job_id,
            artifact_type=artifact_type,
            content_text=json.dumps(content) if content else None,
            file_path=str(file_path) if file_path else None,
            provider=None,
            metadata_json=None,
            created_at=datetime.now()
        )

        with self.db:
            cursor = self.db.conn.cursor()
            cursor.execute("""
                INSERT INTO job_artifacts (
                    job_id, artifact_type, content_text, file_path,
                    provider, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                artifact.job_id, artifact.artifact_type.value,
                artifact.content_text, artifact.file_path,
                artifact.provider, artifact.metadata_json,
                artifact.created_at.isoformat()
            ))
            self.db.conn.commit()
