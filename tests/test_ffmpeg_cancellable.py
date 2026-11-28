"""Tests for cancellable FFmpeg process execution."""

from concurrent.futures import CancelledError
import sys
from threading import Event

import pytest

from app.utils.ffmpeg import FFmpegWrapper


class PythonProcessWrapper(FFmpegWrapper):
    def __init__(self):
        super().__init__(ffmpeg_path=sys.executable)

    def is_available(self):
        return True


def test_cancellable_executor_reports_progress():
    progress = []
    script = (
        "print('out_time_us=250000', flush=True); "
        "print('out_time_us=1000000', flush=True)"
    )

    result = PythonProcessWrapper().execute_cancellable(
        ["-u", "-c", script],
        expected_duration_seconds=1,
        cancel_event=Event(),
        progress_callback=progress.append,
    )

    assert result[0] == 0
    assert progress == [0.25, 1.0, 1.0]


def test_cancellable_executor_terminates_process_when_cancelled():
    cancel_event = Event()
    script = "print('out_time_us=100000', flush=True); import time; time.sleep(20)"

    def cancel_after_progress(_fraction):
        cancel_event.set()

    with pytest.raises(CancelledError):
        PythonProcessWrapper().execute_cancellable(
            ["-u", "-c", script],
            expected_duration_seconds=1,
            cancel_event=cancel_event,
            progress_callback=cancel_after_progress,
            timeout=30,
        )
