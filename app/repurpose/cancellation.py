"""Cooperative cancellation helpers for local repurposing jobs."""

from concurrent.futures import CancelledError
from threading import Event


def raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancellation requested by the user.")
