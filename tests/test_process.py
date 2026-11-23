"""Tests for platform-specific child-process configuration."""

import os
import subprocess

from app.utils.process import hidden_process_creation_flags


def test_hidden_process_creation_flags_match_platform():
    expected = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )

    assert hidden_process_creation_flags() == expected
