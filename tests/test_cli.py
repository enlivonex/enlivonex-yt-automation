"""Tests for CLI."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from app import cli_main
from app.cli_main import app


runner = CliRunner()


def test_cli_init(tmp_path, monkeypatch):
    """Test CLI init command."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Initializing" in result.stdout
    assert "Database initialized" in result.stdout


def test_cli_verify(tmp_path, monkeypatch):
    """Test CLI verify command."""
    monkeypatch.chdir(tmp_path)

    # Init first
    runner.invoke(app, ["init"])

    # Verify
    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 0
    assert "System Verification" in result.stdout


def test_cli_status(tmp_path, monkeypatch):
    """Test CLI status command."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "System Status" in result.stdout


def test_cli_list_jobs(tmp_path, monkeypatch):
    """Test CLI list-jobs command."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["list-jobs"])

    assert result.exit_code == 0


def test_cli_ingest_missing_directory(tmp_path, monkeypatch):
    """Test CLI ingest with missing directory."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["ingest", "nonexistent"])

    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_cli_run_job_reports_runner_failure(monkeypatch):
    def fail_job(self, **kwargs):
        raise RuntimeError("simulated runner failure")

    monkeypatch.setattr(cli_main.LocalJobRunner, "run_job", fail_job)

    result = runner.invoke(app, ["run-job", "--niche", "gaming"])

    assert result.exit_code == 1
    assert "Job failed" in result.stdout
    assert "simulated runner failure" in result.stdout
