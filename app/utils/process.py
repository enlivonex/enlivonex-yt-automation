"""Helpers for launching child processes without Windows console windows."""

import os
import subprocess


def hidden_process_creation_flags() -> int:
    """Return flags that keep child console applications out of the UI."""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
