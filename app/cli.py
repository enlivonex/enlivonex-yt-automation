"""Command-line interface for YouTube automation."""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_settings
from app.database import Database, MediaStatus
from app.pipeline import Pipeline
from app.captions import TemplateCaptionGenerator
from app.utils import setup_logging

app = typer.Typer(help="Enlivonex YouTube Automation CLI")
console = Console()


@app.command()
def scan(
    env_file: str = typer.Option(".env", help="Environment file path")
):
    """Scan Google Drive folder for new media files."""
    settings = load_settings(env_file)
    logger = setup_logging(settings.log_level, settings.log_file)

    errors = settings.validate()
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for error in errors:
            console.print(f"  • {error}")
        raise typer.Exit(1)

    pipeline = Pipeline(settings)
    drive_client = pipeline.get_drive_client()

    if not drive_client:
        console.print("[red]Google Drive not configured[/red]")
        raise typer.Exit(1)

    all_formats = settings.supported_image_formats + settings.supported_video_formats
    files = drive_client.list_files(all_formats)

    console.print(f"\n[green]Found {len(files)} files in Drive folder[/green]\n")

    if files:
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Filename", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Size", style="green")

        for file_info in files:
            filename = file_info['name']
            mime_type = file_info.get('mimeType', 'unknown')
            size_bytes = int(file_info.get('size', 0))
            size_mb = size_bytes / (1024 * 1024)

            table.add_row(
                filename,
                mime_type.split('/')[-1],
                f"{size_mb:.2f} MB"
            )

        console.print(table)


@app.command()
def process_local(
    input_dir: str = typer.Argument(None, help="Input directory with media files"),
    env_file: str = typer.Option(".env", help="Environment file path"),
    force: bool = typer.Option(False, "--force", help="Force reprocessing")
):
    """Process local media files without Drive integration."""
    settings = load_settings(env_file)
    logger = setup_logging(settings.log_level, settings.log_file)

    errors = settings.validate()
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for error in errors:
            console.print(f"  • {error}")
        raise typer.Exit(1)

    # Use provided input_dir or default from settings
    input_path = Path(input_dir) if input_dir else settings.input_dir

    if not input_path.exists():
        console.print(f"[red]Input directory does not exist: {input_path}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Processing local files from: {input_path}[/cyan]\n")

    caption_generator = TemplateCaptionGenerator(
        style=settings.caption_style,
        max_length=settings.caption_max_length
    )

    pipeline = Pipeline(settings)
    count = pipeline.process_local_files(input_path, caption_generator, force)

    console.print(f"\n[green]✓ Successfully processed {count} files[/green]")
    console.print(f"Output directory: {settings.output_dir}")


@app.command()
def run(
    env_file: str = typer.Option(".env", help="Environment file path"),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--no-dry-run", help="Override DRY_RUN setting"),
    upload: bool = typer.Option(False, "--upload", help="Upload processed videos to YouTube"),
    privacy: str = typer.Option("private", help="YouTube privacy status (private/unlisted/public)"),
    confirm_public: bool = typer.Option(False, "--confirm-public", help="Confirm public uploads"),
    force: bool = typer.Option(False, "--force", help="Force reprocessing")
):
    """Run the full automation pipeline: Drive → process → upload."""
    settings = load_settings(env_file)

    # Override DRY_RUN if specified
    if dry_run is not None:
        settings.dry_run = dry_run

    # Override privacy status if specified
    if privacy:
        settings.youtube_privacy_status = privacy

    logger = setup_logging(settings.log_level, settings.log_file)

    errors = settings.validate()
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for error in errors:
            console.print(f"  • {error}")
        raise typer.Exit(1)

    # Safety check for public uploads
    if upload and privacy == "public" and not confirm_public:
        console.print("[red]ERROR: Public upload requires --confirm-public flag[/red]")
        console.print("This prevents accidental public uploads during development.")
        raise typer.Exit(1)

    # DRY_RUN mode indicator
    if settings.dry_run:
        console.print("[yellow]⚠ DRY RUN MODE: YouTube uploads will be skipped[/yellow]\n")

    caption_generator = TemplateCaptionGenerator(
        style=settings.caption_style,
        max_length=settings.caption_max_length
    )

    pipeline = Pipeline(settings)

    console.print("[cyan]Starting automation pipeline...[/cyan]\n")
    stats = pipeline.run_full_pipeline(caption_generator, upload=upload, force=force)

    # Display results
    console.print("\n[bold]Pipeline Results:[/bold]")
    console.print(f"  Discovered: {stats['discovered']}")
    console.print(f"  Downloaded: {stats['downloaded']}")
    console.print(f"  Processed:  {stats['processed']}")
    console.print(f"  Uploaded:   {stats['uploaded']}")
    console.print(f"  Failed:     {stats['failed']}")
    console.print(f"  Skipped:    {stats['skipped']}")

    if stats['processed'] > 0:
        console.print(f"\n[green]✓ Pipeline completed successfully[/green]")
    else:
        console.print(f"\n[yellow]No new files processed[/yellow]")


@app.command()
def status(
    env_file: str = typer.Option(".env", help="Environment file path")
):
    """Show processing status and statistics."""
    settings = load_settings(env_file)

    with Database(settings.database_path) as db:
        db.initialize()

        console.print("\n[bold]Processing Status:[/bold]\n")

        for status in MediaStatus:
            count = db.count_by_status(status)
            if count > 0:
                console.print(f"  {status.value}: {count}")

        # Show recent items
        console.print("\n[bold]Recent Processed Items:[/bold]\n")

        processed_items = db.get_by_status(MediaStatus.PROCESSED)
        uploaded_items = db.get_by_status(MediaStatus.UPLOADED)
        recent = (processed_items + uploaded_items)[:10]

        if recent:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Filename", style="cyan")
            table.add_column("Caption", style="yellow")
            table.add_column("Status", style="green")

            for item in recent:
                table.add_row(
                    item.original_filename[:40],
                    (item.caption or "")[:50],
                    item.status.value
                )

            console.print(table)
        else:
            console.print("  No processed items yet")


@app.command()
def retry_failed(
    env_file: str = typer.Option(".env", help="Environment file path")
):
    """Retry processing of failed items."""
    settings = load_settings(env_file)
    logger = setup_logging(settings.log_level, settings.log_file)

    with Database(settings.database_path) as db:
        db.initialize()

        failed_items = db.get_by_status(MediaStatus.FAILED)

        if not failed_items:
            console.print("[yellow]No failed items to retry[/yellow]")
            return

        console.print(f"[cyan]Found {len(failed_items)} failed items[/cyan]\n")

        for item in failed_items:
            console.print(f"Retrying: {item.original_filename}")
            # Reset status to allow reprocessing
            db.update_status(item.id, MediaStatus.DOWNLOADED, None)

        console.print(f"\n[green]Reset {len(failed_items)} items for retry[/green]")
        console.print("Run 'python -m app run' to reprocess them")


@app.command()
def check_config(
    env_file: str = typer.Option(".env", help="Environment file path")
):
    """Validate configuration and check dependencies."""
    settings = load_settings(env_file)

    console.print("\n[bold]Configuration Check:[/bold]\n")

    errors = settings.validate()
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for error in errors:
            console.print(f"  • {error}")
    else:
        console.print("[green]✓ Configuration valid[/green]")

    console.print("\n[bold]Service Configuration:[/bold]")
    console.print(f"  Google Drive: {'✓' if settings.is_google_configured() else '✗'}")
    console.print(f"  YouTube:      {'✓' if settings.is_youtube_configured() else '✗'}")

    console.print("\n[bold]Directories:[/bold]")
    console.print(f"  Input:  {settings.input_dir} ({'exists' if settings.input_dir.exists() else 'missing'})")
    console.print(f"  Output: {settings.output_dir} ({'exists' if settings.output_dir.exists() else 'missing'})")
    console.print(f"  Temp:   {settings.temp_dir} ({'exists' if settings.temp_dir.exists() else 'missing'})")

    console.print("\n[bold]Settings:[/bold]")
    console.print(f"  DRY_RUN:       {settings.dry_run}")
    console.print(f"  Privacy:       {settings.youtube_privacy_status}")
    console.print(f"  Caption style: {settings.caption_style}")
    console.print(f"  Max length:    {settings.caption_max_length}")

    # Check FFmpeg
    from app.media import MediaProcessor
    processor = MediaProcessor(
        caption_font_size=settings.caption_font_size,
        caption_padding=settings.caption_padding,
        caption_bg_color=settings.caption_bg_color,
        caption_text_color=settings.caption_text_color,
        ffmpeg_path=settings.ffmpeg_path
    )

    console.print("\n[bold]Dependencies:[/bold]")
    if processor.check_ffmpeg_available():
        console.print("  FFmpeg: [green]✓ available[/green]")
    else:
        console.print("  FFmpeg: [red]✗ not found[/red]")
        console.print("    Install FFmpeg for video processing")


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
