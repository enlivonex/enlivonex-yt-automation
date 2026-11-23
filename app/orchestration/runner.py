"""Shared local job entry point for the CLI and desktop UI."""

from pathlib import Path
import sqlite3
import logging
import math
import os
import re
import shutil
import tempfile
import uuid
from typing import Any, Callable, Optional

from ..ai.ollama import OllamaClient
from ..clips.manager import ClipManager
from ..composition.composer import CompositionConfig
from ..clips.selector import (
    CompositeSelector,
    LeastUsedSelector,
    OrientationSelector,
)
from ..config.ai_providers import create_ai_providers
from ..config.settings import load_settings
from ..database.db import Database
from ..metadata.generator import create_metadata_generator
from ..research import ResearchService
from .job import JobOrchestrator

logger = logging.getLogger(__name__)
GENERATED_JOB_RETENTION_COUNT = 10


class LocalJobRunner:
    """Run existing jobs with the same database and components as the CLI."""

    def __init__(
        self,
        database_path: Path = Path("data/media.db"),
        clips_dir: Path = Path("data/clips"),
        jobs_dir: Path = Path("data/jobs"),
    ):
        self.database_path = database_path
        self.clips_dir = clips_dir
        self.jobs_dir = jobs_dir

    @staticmethod
    def create_selector(
        orientation: Optional[str],
    ) -> CompositeSelector:
        selectors = []
        if orientation:
            selectors.append(OrientationSelector(orientation))
        selectors.append(LeastUsedSelector())
        return CompositeSelector(selectors)

    def run_job(
        self,
        orientation: Optional[str] = None,
        dry_run: bool = False,
        on_job_created: Optional[Callable[[str], None]] = None,
        story_duration: float = 30.0,
        story_niche: str = "",
        admin_key: Optional[str] = None,
    ) -> dict:
        if (
            not math.isfinite(story_duration)
            or story_duration < 20
            or story_duration > 90
        ):
            raise ValueError("Story duration must be between 20 and 90 seconds.")
        if not story_niche.strip():
            raise ValueError("Story niche cannot be empty.")
        output_orientation = orientation or "portrait"
        dimensions = {
            "portrait": (1080, 1920),
            "landscape": (1920, 1080),
            "square": (1080, 1080),
        }
        if output_orientation not in dimensions:
            raise ValueError(
                "Output orientation must be portrait, landscape, or square."
            )

        settings = load_settings()

        # Check daily video creation limit (bypassed if in dry_run or admin mode)
        if not dry_run:
            from ..limits import check_creation_limit, DailyLimitExceededError
            allowed, limit_message, _ = check_creation_limit(
                database_path=self.database_path,
                admin_key=admin_key,
                settings=settings,
            )
            if not allowed:
                raise DailyLimitExceededError(limit_message)

        ollama_client = None
        if settings.story_provider == "ollama":
            ollama_client = OllamaClient(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout_seconds=settings.ollama_timeout_seconds,
            )
        embedding_client = OllamaClient(
            base_url=getattr(
                settings, "ollama_base_url", "http://127.0.0.1:11434"
            ),
            model=getattr(
                settings, "ollama_embedding_model", "nomic-embed-text"
            ),
            timeout_seconds=getattr(settings, "ollama_timeout_seconds", 300),
        )
        embedding_model = getattr(
            settings, "ollama_embedding_model", "nomic-embed-text"
        )
        providers = create_ai_providers(settings, ollama_client=ollama_client)
        metadata_generator = create_metadata_generator(
            settings, ollama_client=ollama_client
        )
        selector = self.create_selector(output_orientation)
        width, height = dimensions[output_orientation]
        story_settings = {
            "story_duration": story_duration,
            "story_niche": story_niche,
            "output_orientation": output_orientation,
        }
        clip_manager = ClipManager(Database(self.database_path), self.clips_dir)
        db = clip_manager.db
        research_service = ResearchService(
            db,
            embedding_client,
            embedding_model,
            web_enabled=getattr(settings, "web_research_enabled", False),
        )
        orchestrator = JobOrchestrator(
            db=db,
            clip_manager=clip_manager,
            story_provider=providers.story,
            voice_provider=providers.voice,
            metadata_generator=metadata_generator,
            work_dir=self.jobs_dir,
            research_service=research_service,
        )

        try:
            with db:
                db.initialize()
                job_id = orchestrator.create_job(
                    clip_selector=selector,
                    config=story_settings,
                )
                if on_job_created:
                    on_job_created(job_id)
                result = orchestrator.run_job(
                    job_id,
                    clip_selector=selector,
                    voice_config=providers.voice_config,
                    composition_config=CompositionConfig(width=width, height=height),
                    dry_run=dry_run,
                    story_duration=story_duration,
                    story_niche=story_niche,
                )
        except Exception as job_error:
            unload_errors = self._unload_ollama_clients(
                ollama_client, embedding_client
            )
            if unload_errors:
                raise RuntimeError(
                    f"{job_error} Also, Ollama could not confirm model unload: "
                    + "; ".join(unload_errors)
                ) from job_error
            raise
        else:
            unload_errors = self._unload_ollama_clients(
                ollama_client, embedding_client
            )
            if unload_errors:
                result["warnings"] = [
                    "Video job completed, but Ollama could not confirm model "
                    "unload: " + "; ".join(unload_errors)
                ]

        output_path = self.jobs_dir / result["job_id"] / "output.mp4"
        result["output_path"] = str(output_path) if output_path.exists() else None
        if result.get("status") == "completed" and output_path.is_file():
            from ..limits import record_video_creation
            record_video_creation(self.database_path, result["job_id"])
            result["retention_warnings"] = self._prune_old_generated_jobs()
        return result

    def _prune_old_generated_jobs(self) -> list[str]:
        """Keep the newest completed generated videos and remove older job folders."""
        if not self.jobs_dir.is_dir():
            return []

        with Database(self.database_path) as db:
            db.initialize()
            rows = db.conn.execute(
                "SELECT job_id FROM jobs WHERE status = ?",
                ("COMPLETED",),
            ).fetchall()
        completed_job_ids = {row["job_id"] for row in rows}

        completed_outputs = []
        for job_directory in self.jobs_dir.iterdir():
            if not job_directory.is_dir() or not job_directory.name.startswith("JOB-"):
                continue
            if job_directory.name not in completed_job_ids:
                continue
            output_path = job_directory / "output.mp4"
            try:
                if output_path.is_file() and output_path.stat().st_size > 0:
                    completed_outputs.append(
                        (output_path.stat().st_mtime, job_directory)
                    )
            except OSError:
                logger.warning(
                    "Could not inspect generated output for retention: %s",
                    output_path,
                    exc_info=True,
                )

        completed_outputs.sort(key=lambda item: item[0], reverse=True)
        warnings = []
        jobs_root = self.jobs_dir.resolve()
        for _, job_directory in completed_outputs[GENERATED_JOB_RETENTION_COUNT:]:
            resolved_directory = job_directory.resolve()
            if (
                resolved_directory.parent != jobs_root
                or not resolved_directory.name.startswith("JOB-")
            ):
                warnings.append(
                    f"Skipped unexpected generated-job path: {job_directory}"
                )
                continue
            try:
                shutil.rmtree(resolved_directory)
            except OSError as error:
                logger.exception(
                    "Could not remove old generated job folder %s",
                    resolved_directory,
                )
                warnings.append(
                    f"Could not remove old generated job folder "
                    f"{resolved_directory.name}: {error}"
                )
        return warnings

    @staticmethod
    def _unload_ollama_clients(*clients: Optional[OllamaClient]) -> list[str]:
        errors = []
        for client in clients:
            if client is None:
                continue
            try:
                client.unload()
            except Exception as error:
                logger.exception("Failed to unload Ollama model after job.")
                errors.append(str(error))
        return errors

    def ingest_research_directory(self, directory: Path) -> dict[str, Any]:
        """Index local text, Markdown, and PDF documents for RAG."""
        settings = load_settings()
        embedding_client = OllamaClient(
            base_url=settings.ollama_base_url,
            model=getattr(
                settings, "ollama_embedding_model", "nomic-embed-text"
            ),
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        db = Database(self.database_path)
        try:
            with db:
                db.initialize()
                result = ResearchService(
                    db,
                    embedding_client,
                    getattr(
                        settings, "ollama_embedding_model", "nomic-embed-text"
                    ),
                    web_enabled=getattr(
                        settings, "web_research_enabled", False
                    ),
                ).ingest_directory(directory)
        except Exception as ingest_error:
            unload_errors = self._unload_ollama_clients(embedding_client)
            if unload_errors:
                raise RuntimeError(
                    f"{ingest_error} Also, Ollama could not confirm model unload: "
                    + "; ".join(unload_errors)
                ) from ingest_error
            raise
        else:
            unload_errors = self._unload_ollama_clients(embedding_client)
            if unload_errors:
                result["warnings"] = unload_errors
            return result

    def ingest_directory(self, directory: Path, recursive: bool = False) -> int:
        """Copy external videos into the managed library and register them."""
        source_directory = Path(directory).expanduser().resolve()
        if not source_directory.is_dir():
            raise FileNotFoundError(f"Directory not found: {source_directory}")

        supported_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
        file_pattern = "**/*" if recursive else "*"
        source_files = sorted(
            path
            for path in source_directory.glob(file_pattern)
            if path.is_file() and path.suffix.lower() in supported_extensions
        )

        db = Database(self.database_path)
        with db:
            db.initialize()
            manager = ClipManager(db, self.clips_dir)
            imported = 0
            failures = []

            for source_path in source_files:
                try:
                    file_hash = Database.hash_file(source_path)
                    existing = db.get_clip_by_hash(file_hash)
                    if existing:
                        if existing.id is None:
                            raise RuntimeError(
                                f"Clip record has no ID: {existing.filename}"
                            )
                        existing_path = Path(existing.file_path)
                        if (
                            existing_path.is_file()
                            and existing_path.resolve().is_relative_to(
                                self.clips_dir.resolve()
                            )
                            and Database.hash_file(existing_path) == file_hash
                        ):
                            continue

                    managed_path = self._stage_clip(source_path, file_hash)
                    if existing:
                        if Path(existing.file_path).resolve() != managed_path.resolve():
                            db.update_clip_location(
                                existing.id,
                                managed_path.name,
                                str(managed_path.resolve()),
                                managed_path.stat().st_size,
                            )
                        continue

                    manager.ingest_clip(managed_path)
                    imported += 1
                except Exception as error:
                    failures.append(f"{source_path.name}: {error}")

            if failures:
                details = "; ".join(failures[:5])
                if len(failures) > 5:
                    details += f"; and {len(failures) - 5} more"
                raise RuntimeError(
                    f"Imported {imported} clip(s), but could not process "
                    f"{len(failures)} file(s): {details}"
                )
        return imported

    def _stage_clip(self, source_path: Path, file_hash: str) -> Path:
        """Copy a source without overwriting different content in the library."""
        clips_dir = self.clips_dir.resolve()
        clips_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_path.resolve()

        if source_path.is_relative_to(clips_dir):
            return source_path

        target_path = clips_dir / source_path.name
        if target_path.exists():
            if Database.hash_file(target_path) == file_hash:
                return target_path
            target_path = clips_dir / (
                f"{source_path.stem}-{file_hash[:12]}{source_path.suffix}"
            )
            suffix = 2
            while target_path.exists():
                if Database.hash_file(target_path) == file_hash:
                    return target_path
                target_path = clips_dir / (
                    f"{source_path.stem}-{file_hash[:12]}-{suffix}"
                    f"{source_path.suffix}"
                )
                suffix += 1

        temporary_path = clips_dir / f".import-{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(source_path, temporary_path)
            if target_path.exists():
                if Database.hash_file(target_path) == file_hash:
                    return target_path
                raise FileExistsError(
                    f"Destination appeared during import: {target_path.name}"
                )
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target_path

    def ingest_drive_folder(self, folder_id: str) -> int:
        """Download supported videos from a configured Google Drive folder."""
        folder_id = folder_id.strip()
        if not folder_id:
            raise ValueError("Google Drive folder ID cannot be empty.")

        settings = load_settings()
        if not settings.google_client_id or not settings.google_client_secret:
            raise RuntimeError(
                "Cloud Google Drive import is not configured for this build. "
                "Use a Google Drive for desktop synced folder with local import, "
                "or ask the app publisher to configure its Google Drive OAuth "
                "client and complete any required Google verification."
            )

        from ..drive.client import DriveClient

        client = DriveClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            token_file=settings.google_token_file,
            folder_id=folder_id,
        )
        if not client.authenticate(raise_errors=True):
            raise RuntimeError("Could not authenticate with Google Drive.")
        if not client.verify_folder_access(raise_errors=True):
            raise RuntimeError(
                "The connected Google account cannot access that Drive folder."
            )

        supported_formats = ["mp4", "mov", "avi", "mkv", "webm", "flv"]
        drive_files = client.list_files(
            supported_formats, raise_errors=True
        )
        if not drive_files:
            return 0

        downloaded = 0
        self.clips_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="drive-import-", dir=self.clips_dir.parent
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            for drive_file in drive_files:
                file_id = drive_file.get("id")
                filename = drive_file.get("name")
                if not file_id or not filename:
                    raise RuntimeError(
                        "Google Drive returned a file without an ID or name."
                    )
                extension = Path(filename.replace("\\", "/")).suffix.lower()
                if extension not in {f".{item}" for item in supported_formats}:
                    continue
                safe_file_id = re.sub(r"[^A-Za-z0-9_-]", "", file_id)
                if not safe_file_id:
                    raise RuntimeError(
                        f"Google Drive returned an invalid file ID for {filename}."
                    )
                download_directory = temporary_path / safe_file_id
                download_directory.mkdir()
                download_name = Path(filename.replace("\\", "/")).name
                target = client.download_file(
                    file_id,
                    download_name,
                    download_directory,
                    raise_errors=True,
                )
                if target is None:
                    raise RuntimeError(
                        f"Google Drive did not download {filename}."
                    )
                downloaded += 1

            if not downloaded:
                return 0
            return self.ingest_directory(temporary_path, recursive=True)

    def get_job_stage(self, job_id: str) -> Optional[str]:
        conn = sqlite3.connect(self.database_path, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT current_stage FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def get_recent_outputs(self, limit: int = 20) -> list[Path]:
        """Return recent non-empty generated videos from completed job folders."""
        if limit < 1 or not self.jobs_dir.exists():
            return []

        outputs = []
        for path in self.jobs_dir.glob("JOB-*/output.mp4"):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    outputs.append(path)
            except OSError:
                continue

        def modified_time(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        return sorted(outputs, key=modified_time, reverse=True)[:limit]
