"""Tests for upload progress and stalled resumable upload handling."""

import pytest

from app.database.db import Database
from app.metadata.models import YouTubeMetadata
from app.youtube.uploader import YouTubeUploader


class FakeStatus:
    def __init__(self, progress):
        self.value = progress

    def progress(self):
        return self.value


class FakeRequest:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = 0

    def next_chunk(self, num_retries):
        assert num_retries == 2
        self.calls += 1
        result = next(self.steps)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeVideos:
    def __init__(self, request):
        self.request = request

    def insert(self, **_kwargs):
        return self.request


class FakeYouTube:
    def __init__(self, request):
        self.request = request

    def videos(self):
        return FakeVideos(self.request)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.db")
    with db:
        db.initialize()
        yield db


def setup_media_upload(monkeypatch, request, database):
    import googleapiclient.http

    monkeypatch.setattr(
        googleapiclient.http,
        "MediaFileUpload",
        lambda *_args, **_kwargs: object(),
    )
    uploader = YouTubeUploader(database, no_progress_timeout_seconds=60)
    uploader._youtube = FakeYouTube(request)
    return uploader


def test_upload_reports_real_chunk_progress(
    monkeypatch, database, tmp_path
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    request = FakeRequest(
        [
            (FakeStatus(0.5), None),
            (FakeStatus(0.9), None),
            (None, {"id": "uploaded-video"}),
        ]
    )
    uploader = setup_media_upload(monkeypatch, request, database)
    progress = []

    with database:
        video_id = uploader.upload_video(
            video,
            YouTubeMetadata("Title", "Description", []),
            "UPLOAD-TEST",
            progress_callback=progress.append,
        )

    assert video_id == "uploaded-video"
    assert progress == [50, 90, 100]


def test_upload_retries_read_timeout_on_same_resumable_request(
    monkeypatch, database, tmp_path
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    request = FakeRequest(
        [
            TimeoutError("The read operation timed out"),
            (FakeStatus(0.5), None),
            (None, {"id": "uploaded-video"}),
        ]
    )
    uploader = setup_media_upload(monkeypatch, request, database)
    monkeypatch.setattr("app.youtube.uploader.time.sleep", lambda _seconds: None)
    progress = []

    with database:
        video_id = uploader.upload_video(
            video,
            YouTubeMetadata("Title", "Description", []),
            "UPLOAD-TIMEOUT-RETRY",
            progress_callback=progress.append,
        )

    assert video_id == "uploaded-video"
    assert request.calls == 3
    assert progress == [50, 100]


def test_upload_times_out_after_retry_window_without_progress(
    monkeypatch, database, tmp_path
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    request = FakeRequest([TimeoutError("read timed out")])
    uploader = setup_media_upload(monkeypatch, request, database)
    uploader.no_progress_timeout_seconds = -1

    with database, pytest.raises(RuntimeError, match="resumable upload was retried"):
        uploader.upload_video(
            video,
            YouTubeMetadata("Title", "Description", []),
            "UPLOAD-TIMEOUT-STOP",
        )

    assert request.calls == 1


def test_upload_fails_instead_of_waiting_forever_on_stall(
    monkeypatch, database, tmp_path
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    request = FakeRequest([(None, None)])
    uploader = setup_media_upload(monkeypatch, request, database)
    uploader.no_progress_timeout_seconds = -1

    with database, pytest.raises(RuntimeError, match="no progress"):
        uploader.upload_video(
            video,
            YouTubeMetadata("Title", "Description", []),
            "UPLOAD-STALLED",
        )


def test_upload_http_does_not_treat_resumable_308_as_redirect(database):
    uploader = YouTubeUploader(database, request_timeout_seconds=17)
    http = uploader._create_media_http()

    assert 308 not in http.redirect_codes
    assert 301 in http.redirect_codes
    assert http.timeout == 17
