"""Tests for local YouTube account profiles and token protection."""

import json
import os
from unittest.mock import Mock

import pytest

from app.youtube.accounts import (
    YouTubeAccountStore,
    read_protected_token,
    write_protected_token,
)
from app.database.db import Database
from app.youtube.uploader import YouTubeUploader


def test_account_profiles_add_select_and_remove(tmp_path):
    store = YouTubeAccountStore(tmp_path / "accounts")
    first = store.add_account("Personal channel")
    second = store.add_account("Creator channel")

    assert store.active_account_id == second.account_id
    assert store.select_account(first.account_id) == first
    assert store.active_account_id == first.account_id

    token_path = store.token_path(first.account_id)
    write_protected_token(token_path, json.dumps({"refresh_token": "secret"}))
    store.remove_account(first.account_id)

    assert store.accounts() == [second]
    assert store.active_account_id == second.account_id
    assert not token_path.exists()


def test_account_profiles_reject_duplicate_names_and_invalid_ids(tmp_path):
    store = YouTubeAccountStore(tmp_path / "accounts")
    account = store.add_account("Main")

    with pytest.raises(ValueError, match="already exists"):
        store.add_account("main")
    with pytest.raises(ValueError, match="Invalid"):
        store.token_path("..\\outside")

    assert store.accounts() == [account]


def test_token_storage_round_trips_and_does_not_store_plaintext(tmp_path):
    token_path = tmp_path / "main.token"
    token_json = json.dumps(
        {"refresh_token": "sensitive-refresh-token", "token": "access-token"}
    )

    write_protected_token(token_path, token_json)

    assert read_protected_token(token_path)["refresh_token"] == (
        "sensitive-refresh-token"
    )
    if os.name == "nt":
        assert b"sensitive-refresh-token" not in token_path.read_bytes()


def test_legacy_token_migrates_to_protected_default_profile(tmp_path):
    legacy_path = tmp_path / "youtube_token.json"
    legacy_path.write_text(
        json.dumps({"refresh_token": "legacy-refresh-token"}),
        encoding="utf-8",
    )
    store = YouTubeAccountStore(
        tmp_path / "accounts", legacy_token_path=legacy_path
    )

    account = store.migrate_legacy_token()

    assert account is not None
    assert account.label == "Default YouTube account"
    assert not legacy_path.exists()
    assert read_protected_token(store.token_path(account.account_id))[
        "refresh_token"
    ] == "legacy-refresh-token"


def test_corrupt_account_manifest_is_reported(tmp_path):
    directory = tmp_path / "accounts"
    directory.mkdir()
    (directory / "accounts.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Could not read"):
        YouTubeAccountStore(directory)


def test_upload_authentication_does_not_open_a_new_oauth_flow(
    tmp_path, monkeypatch
):
    credentials_path = tmp_path / "oauth-client.json"
    credentials_path.write_text("{}", encoding="utf-8")
    uploader = YouTubeUploader(
        Database(tmp_path / "media.db"),
        credentials_path=credentials_path,
        token_path=tmp_path / "missing.token",
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        lambda *_args, **_kwargs: pytest.fail(
            "Upload must not launch an unexpected OAuth account flow"
        ),
    )

    with pytest.raises(RuntimeError, match="Connect it from YouTube Setup"):
        uploader.authenticate(allow_new_authorization=False)


def test_new_oauth_flow_prompts_user_to_choose_google_account(
    tmp_path, monkeypatch
):
    credentials_path = tmp_path / "oauth-client.json"
    credentials_path.write_text("{}", encoding="utf-8")
    credentials = Mock(valid=True)
    credentials.to_json.return_value = json.dumps({"refresh_token": "refresh"})
    flow = Mock()
    flow.run_local_server.return_value = credentials
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        lambda *_args, **_kwargs: flow,
    )
    monkeypatch.setattr(
        "google_auth_httplib2.AuthorizedHttp",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *_args, **_kwargs: object(),
    )
    uploader = YouTubeUploader(
        Database(tmp_path / "media.db"),
        credentials_path=credentials_path,
        token_path=tmp_path / "accounts" / "account.token",
    )
    monkeypatch.setattr(uploader, "_create_media_http", lambda _module: object())

    uploader.authenticate()

    flow.run_local_server.assert_called_once_with(
        port=0,
        prompt="select_account",
        timeout_seconds=300,
    )


def test_oauth_connection_retries_timeout_then_returns_result(monkeypatch):
    from requests.exceptions import ConnectTimeout

    from app.youtube import uploader as uploader_module

    attempts = []
    delays = []

    def operation():
        attempts.append(None)
        if len(attempts) < 3:
            raise ConnectTimeout("temporary timeout")
        return "token"

    monkeypatch.setattr(uploader_module.time, "sleep", delays.append)

    assert uploader_module._retry_oauth_connection(operation) == "token"
    assert len(attempts) == 3
    assert delays == [1, 2]


def test_oauth_connection_reports_actionable_error_after_retries(monkeypatch):
    from requests.exceptions import ConnectTimeout

    from app.youtube import uploader as uploader_module

    monkeypatch.setattr(uploader_module.time, "sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="oauth2.googleapis.com"):
        uploader_module._retry_oauth_connection(
            lambda: (_ for _ in ()).throw(ConnectTimeout("network unavailable"))
        )
