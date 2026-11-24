"""Tests for preparing local repurpose drafts in the existing upload review."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.pyside_gui import PySideDashboard
from app.repurpose.editing import RenderedClip


@pytest.fixture(scope="module")
def qapplication():
    return QApplication.instance() or QApplication([])


def test_repurpose_draft_enters_upload_review_as_private(
    tmp_path, monkeypatch, qapplication
):
    monkeypatch.chdir(tmp_path)
    window = PySideDashboard()
    draft = tmp_path / "draft.mp4"
    draft.write_bytes(b"rendered draft")
    (tmp_path / "metadata.json").write_text(
        '{"snippet":{"title":"Unrelated sidecar"},'
        '"status":{"privacyStatus":"public"}}',
        encoding="utf-8",
    )

    page = window.repurpose_page
    page._operation_name = "render"
    page._operation_succeeded(
        RenderedClip(draft.resolve(), 30, 720, 1280, True, 2)
    )

    page.prepare_upload_button.click()

    assert window.pages.currentIndex() != 2
    assert "edit and publish" in page.status.text()

    page.rights_confirmation.setChecked(True)
    page.prepare_upload_button.click()

    assert window.pages.currentIndex() == 2
    assert window.upload_path == draft
    assert window.upload_title.text() == "draft"
    assert window.upload_visibility.currentText() == "private"
    assert window.upload_description.toPlainText() == ""
    assert not window.active_task
    window.close()
    qapplication.processEvents()
