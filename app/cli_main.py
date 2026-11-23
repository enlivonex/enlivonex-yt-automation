"""CLI entry point for Enlivonex YouTube Automation."""

import sys
import json
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from app.database.db import Database
from app.clips.manager import ClipManager
from app.config.ai_providers import ProviderConfigurationError
from app.orchestration.runner import LocalJobRunner
from app.youtube.uploader import MockYouTubeUploader
from app.youtube.analytics import MockYouTubeAnalytics
from app.dashboard.app import run_dashboard, validate_dashboard_host

app = typer.Typer(help="Enlivonex YouTube Automation")
console = Console()

# Default paths
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "media.db"
CLIPS_DIR = DATA_DIR / "clips"
JOBS_DIR = DATA_DIR / "jobs"


@app.command()
def init():
    """Initialize the automation platform."""
    console.print("[bold blue]Initializing Enlivonex YouTube Automation...[/bold blue]")

    # Create directories
    DATA_DIR.mkdir(exist_ok=True)
    CLIPS_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)

    # Initialize database
    db = Database(DB_PATH)
    with db:
        db.initialize()

    console.print("[bold green][OK][/bold green] Directories created")
    console.print("[bold green][OK][/bold green] Database initialized")
    console.print(f"[bold green][OK][/bold green] Ready to use!")
    console.print(f"\nDatabase: {DB_PATH}")
    console.print(f"Clips directory: {CLIPS_DIR}")
    console.print(f"Jobs directory: {JOBS_DIR}")


@app.command()
def ingest(
    directory: Path = typer.Argument(..., help="Directory containing video clips"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Scan subdirectories")
):
    """Ingest video clips into the library."""
    if not directory.exists():
        console.print(f"[bold red]Error:[/bold red] Directory not found: {directory}")
        raise typer.Exit(1)

    console.print(f"[bold blue]Ingesting clips from:[/bold blue] {directory}")

    db = Database(DB_PATH)
    clip_manager = ClipManager(db, CLIPS_DIR)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Scanning and ingesting clips...", total=None)

        with db:
            db.initialize()
            clips = clip_manager.ingest_directory(directory, recursive)

        progress.update(task, completed=True)

    console.print(f"[bold green][OK][/bold green] Ingested {len(clips)} new clips")


@app.command()
def status():
    """Show automation status."""
    db = Database(DB_PATH)

    with db:
        db.initialize()
        cursor = db.conn.cursor()

        # Clips stats
        cursor.execute("""
            SELECT status, COUNT(*) FROM source_clips
            GROUP BY status
        """)
        clips_by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # Jobs stats
        cursor.execute("""
            SELECT status, COUNT(*) FROM jobs
            GROUP BY status
        """)
        jobs_by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # Latest job
        cursor.execute("""
            SELECT job_id, status, current_stage, started_at
            FROM jobs
            ORDER BY started_at DESC
            LIMIT 1
        """)
        latest_job = cursor.fetchone()

        # Videos
        cursor.execute("SELECT COUNT(*) FROM youtube_videos")
        video_count = cursor.fetchone()[0]

    # Display
    console.print("\n[bold cyan]== System Status ==[/bold cyan]\n")

    # Clips table
    clips_table = Table(title="Clip Library")
    clips_table.add_column("Status", style="cyan")
    clips_table.add_column("Count", justify="right", style="green")

    for status, count in clips_by_status.items():
        clips_table.add_row(status, str(count))

    console.print(clips_table)

    # Jobs table
    jobs_table = Table(title="Jobs")
    jobs_table.add_column("Status", style="cyan")
    jobs_table.add_column("Count", justify="right", style="green")

    for status, count in jobs_by_status.items():
        jobs_table.add_row(status, str(count))

    console.print(jobs_table)

    # Latest job
    if latest_job:
        console.print(f"\n[bold]Latest Job:[/bold] {latest_job[0]}")
        console.print(f"Status: {latest_job[1]} | Stage: {latest_job[2]}")

    console.print(f"\n[bold]Uploaded Videos:[/bold] {video_count}")


@app.command()
def run_job(
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without uploading"),
    orientation: str = typer.Option(
        "portrait", "--orientation", help="Output orientation (portrait/landscape/square)"
    ),
    story_duration: float = typer.Option(
        30.0, "--duration", min=20.0, max=90.0, help="Target video duration in seconds"
    ),
    story_niche: str = typer.Option(
        ..., "--niche", help="Content niche for the generated story and metadata"
    ),
    admin_key: Optional[str] = typer.Option(
        None, "--admin-key", help="Administrator key for unlimited video creation"
    ),
):
    """Run a complete automation job."""
    console.print("[bold blue]Starting automation job...[/bold blue]")

    runner = LocalJobRunner(DB_PATH, CLIPS_DIR, JOBS_DIR)
    try:
        from .limits import DailyLimitExceededError
        result = runner.run_job(
            orientation=orientation,
            dry_run=dry_run,
            story_duration=story_duration,
            story_niche=story_niche,
            admin_key=admin_key,
            on_job_created=lambda job_id: console.print(
                f"[bold green]Job created:[/bold green] {job_id}"
            ),
        )
    except DailyLimitExceededError as e:
        console.print(f"[bold red]Daily limit exceeded:[/bold red] {e}")
        raise typer.Exit(3)
    except ProviderConfigurationError as e:
        console.print(f"[bold red]AI provider configuration error:[/bold red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        console.print(f"[bold red]FAIL Job failed:[/bold red] {e}")
        raise typer.Exit(1)

    try:
        console.print(f"\n[bold green]Job completed successfully![/bold green]")
        console.print(f"Job ID: {result['job_id']}")
        console.print(f"Source: {result['clip']}")
        if len(result.get("clips", [])) > 1:
            console.print(f"Additional clips: {', '.join(result['clips'][1:])}")
        console.print(f"Story: {result['story_title']}")
        if result.get("narration_duration") is not None:
            console.print(
                f"Duration: {result['narration_duration']:.1f}s "
                f"(requested {result['target_story_duration']:.0f}s)"
            )
            if not result.get("duration_within_tolerance", True):
                console.print(
                    "[yellow]Warning: generated duration is outside the "
                    "+/-3.5 second tolerance.[/yellow]"
                )
        console.print(f"Artifacts: {result['artifacts_dir']}")

        if dry_run:
            console.print("\n[yellow]Note: Dry run mode - video not composed[/yellow]")

    except Exception as e:
        console.print(f"[bold red]FAIL Job failed:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def list_jobs(limit: int = typer.Option(10, "--limit", "-n", help="Number of jobs to show")):
    """List recent jobs."""
    db = Database(DB_PATH)

    with db:
        db.initialize()
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT job_id, status, current_stage, started_at, completed_at
            FROM jobs
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))

        jobs = cursor.fetchall()

    if not jobs:
        console.print("[yellow]No jobs found[/yellow]")
        return

    table = Table(title=f"Recent Jobs (showing {len(jobs)})")
    table.add_column("Job ID", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Stage")
    table.add_column("Started")

    for job in jobs:
        from datetime import datetime
        started = datetime.fromisoformat(job[3]).strftime("%Y-%m-%d %H:%M")
        table.add_row(job[0], job[1], job[2], started)

    console.print(table)


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(5000, "--port", "-p", help="Port to bind to")
):
    """Start the web dashboard."""
    try:
        host = validate_dashboard_host(host)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--host") from error
    console.print(f"[bold blue]Starting dashboard at http://{host}:{port}[/bold blue]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        run_dashboard(DB_PATH, CLIPS_DIR, JOBS_DIR, host=host, port=port, debug=False)
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped[/yellow]")


@app.command()
def verify():
    """Verify system configuration."""
    console.print("[bold blue]Verifying system configuration...[/bold blue]\n")

    checks = []

    # Check Python version
    import sys
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(("Python 3.14+", py_version.startswith("3.14"), py_version))

    # Check directories
    checks.append(("Data directory", DATA_DIR.exists(), str(DATA_DIR)))
    checks.append(("Clips directory", CLIPS_DIR.exists(), str(CLIPS_DIR)))
    checks.append(("Jobs directory", JOBS_DIR.exists(), str(JOBS_DIR)))

    # Check database
    checks.append(("Database", DB_PATH.exists(), str(DB_PATH)))

    # Check FFmpeg
    from app.utils.ffmpeg import FFmpegWrapper
    ffmpeg = FFmpegWrapper()
    checks.append(("FFmpeg available", ffmpeg.is_available(), "Required for video processing"))

    # Display results
    table = Table(title="System Verification")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details")

    for name, passed, details in checks:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(name, status, details)

    console.print(table)

    # Summary
    passed_count = sum(1 for _, passed, _ in checks if passed)
    total_count = len(checks)

    if passed_count == total_count:
        console.print(f"\n[bold green]All checks passed ({passed_count}/{total_count})[/bold green]")
    else:
        console.print(f"\n[bold yellow]Some checks failed ({passed_count}/{total_count})[/bold yellow]")
        if not ffmpeg.is_available():
            console.print("\n[yellow]Note: FFmpeg is not installed. Video processing will fail.[/yellow]")
            console.print("Install FFmpeg: https://ffmpeg.org/download.html")


if __name__ == "__main__":
    app()
