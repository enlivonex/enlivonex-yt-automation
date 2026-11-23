"""Tests for safe Google Drive listing and download behavior."""

from pathlib import Path

from app.drive import client as drive_client_module
from app.drive.client import DriveClient


class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Files:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        page_index = len(self.list_calls) - 1
        return _Request(self.pages[page_index])

    def get_media(self, fileId):
        return fileId


class _Service:
    def __init__(self, pages=None):
        self.files_api = _Files(pages)

    def files(self):
        return self.files_api


def test_list_files_reads_all_pages_and_filters_by_extension():
    service = _Service(
        [
            {
                "files": [
                    {"id": "first", "name": "first.mp4"},
                    {"id": "document", "name": "notes.txt"},
                ],
                "nextPageToken": "next",
            },
            {"files": [{"id": "second", "name": "second.mov"}]},
        ]
    )
    client = DriveClient("id", "secret", Path("token.json"), "folder")
    client.service = service

    files = client.list_files(["mp4", "mov"], page_size=10)

    assert [item["id"] for item in files] == ["first", "second"]
    assert service.files_api.list_calls[1]["pageToken"] == "next"
    assert service.files_api.list_calls[0]["supportsAllDrives"] is True


def test_download_file_streams_to_safe_name_without_overwriting(
    monkeypatch, tmp_path
):
    class FakeDownloader:
        def __init__(self, output, _request):
            self.output = output

        def next_chunk(self):
            self.output.write(b"downloaded content")
            return None, True

    monkeypatch.setattr(
        drive_client_module, "MediaIoBaseDownload", FakeDownloader
    )
    client = DriveClient("id", "secret", tmp_path / "token.json", "folder")
    client.service = _Service()
    output_dir = tmp_path / "downloads"

    downloaded = client.download_file(
        "drive-id", r"..\safe.mp4", output_dir, raise_errors=True
    )

    assert downloaded == output_dir / "safe.mp4"
    assert downloaded.read_bytes() == b"downloaded content"

    existing = output_dir / "safe.mp4"
    assert client.download_file("drive-id", "safe.mp4", output_dir) is None
    assert existing.read_bytes() == b"downloaded content"
