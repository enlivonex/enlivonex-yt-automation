"""Local YouTube account profiles and protected OAuth token storage."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid

_TOKEN_HEADER = b"ENLIVONEX-DPAPI\x00"
_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class YouTubeAccount:
    account_id: str
    label: str
    reauth_required: bool = False


def default_youtube_accounts_directory() -> Path:
    """Return a stable per-user directory for OAuth profiles and tokens."""
    if os.name == "nt":
        root = Path(
            os.environ.get(
                "LOCALAPPDATA", Path.home() / "AppData" / "Local"
            )
        )
    else:
        root = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
    return root / "Enlivonex" / "YouTube Automation" / "youtube_accounts"


class YouTubeAccountStore:
    """Manage named account profiles without storing OAuth secrets in the manifest."""

    def __init__(
        self, directory: Path, legacy_token_path: Path | None = None
    ):
        self.directory = Path(directory)
        self.manifest_path = self.directory / "accounts.json"
        self.legacy_token_path = (
            Path(legacy_token_path) if legacy_token_path else None
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._manifest = self._read_manifest()

    def accounts(self) -> list[YouTubeAccount]:
        return [
            YouTubeAccount(
                item["account_id"],
                item["label"],
                item.get("reauth_required", False),
            )
            for item in self._manifest["accounts"]
        ]

    @property
    def active_account_id(self) -> str | None:
        active_id = self._manifest.get("active_account_id")
        return active_id if self._contains(active_id) else None

    def add_account(self, label: str) -> YouTubeAccount:
        label = label.strip()
        if not label or len(label) > 60:
            raise ValueError("Account name must contain 1 to 60 characters.")
        if any(
            item["label"].casefold() == label.casefold()
            for item in self._manifest["accounts"]
        ):
            raise ValueError("An account with that name already exists.")

        account = YouTubeAccount(uuid.uuid4().hex, label)
        self._manifest["accounts"].append(
            {
                "account_id": account.account_id,
                "label": account.label,
                "reauth_required": False,
            }
        )
        self._manifest["active_account_id"] = account.account_id
        self._write_manifest()
        return account

    def select_account(self, account_id: str) -> YouTubeAccount:
        item = self._find(account_id)
        self._manifest["active_account_id"] = item["account_id"]
        self._write_manifest()
        return YouTubeAccount(item["account_id"], item["label"])

    def remove_account(self, account_id: str) -> None:
        item = self._find(account_id)
        token_path = self.token_path(item["account_id"])
        self._manifest["accounts"].remove(item)
        if self._manifest.get("active_account_id") == account_id:
            self._manifest["active_account_id"] = (
                self._manifest["accounts"][-1]["account_id"]
                if self._manifest["accounts"]
                else None
            )
        self._write_manifest()
        try:
            token_path.unlink(missing_ok=True)
        except OSError as error:
            raise RuntimeError(
                f"Removed account profile but could not delete its token file: "
                f"{error}"
            ) from error

    def set_reauth_required(
        self, account_id: str, required: bool
    ) -> YouTubeAccount:
        if not isinstance(required, bool):
            raise ValueError("Reauthorization state must be a boolean.")
        item = self._find(account_id)
        item["reauth_required"] = required
        self._write_manifest()
        return YouTubeAccount(
            item["account_id"], item["label"], item["reauth_required"]
        )

    def migrate_legacy_profiles(self, directories: list[Path]) -> int:
        """Copy the first existing legacy profile store into this stable store."""
        if self._manifest["accounts"]:
            return 0
        target = self.directory.resolve()
        for directory in directories:
            legacy_directory = Path(directory).expanduser()
            legacy_manifest = legacy_directory / "accounts.json"
            if (
                not legacy_manifest.is_file()
                or legacy_directory.resolve() == target
            ):
                continue
            legacy = YouTubeAccountStore(legacy_directory)
            if not legacy._manifest["accounts"]:
                continue
            copied_tokens = []
            original_manifest = self._manifest
            try:
                for account in legacy.accounts():
                    source_token = legacy.token_path(account.account_id)
                    if source_token.is_symlink():
                        raise RuntimeError(
                            "Legacy YouTube token files cannot be symbolic links."
                        )
                    if source_token.is_file():
                        destination_token = (
                            self.directory / f"{account.account_id}.token"
                        )
                        self._atomic_write(
                            destination_token, source_token.read_bytes()
                        )
                        copied_tokens.append(destination_token)
                self._manifest = legacy._manifest
                self._write_manifest()
            except (OSError, RuntimeError, ValueError):
                self._manifest = original_manifest
                for token_path in copied_tokens:
                    token_path.unlink(missing_ok=True)
                raise
            return len(self._manifest["accounts"])
        return 0

    def token_path(self, account_id: str) -> Path:
        self._find(account_id)
        return self.directory / f"{account_id}.token"

    def migrate_legacy_token(self) -> YouTubeAccount | None:
        """Protect and import the old single-account token, if present."""
        if (
            self.legacy_token_path is None
            or not self.legacy_token_path.is_file()
            or self._manifest["accounts"]
        ):
            return None
        try:
            token_text = self.legacy_token_path.read_text(encoding="utf-8")
            token_data = json.loads(token_text)
            if not isinstance(token_data, dict):
                raise ValueError("Legacy OAuth token must contain a JSON object.")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError(
                f"Could not migrate the existing YouTube token: {error}"
            ) from error

        account = self.add_account("Default YouTube account")
        try:
            write_protected_token(
                self.token_path(account.account_id),
                json.dumps(token_data),
            )
            self.legacy_token_path.unlink()
        except (OSError, RuntimeError, ValueError):
            self._manifest["accounts"] = [
                item
                for item in self._manifest["accounts"]
                if item["account_id"] != account.account_id
            ]
            self._manifest["active_account_id"] = None
            self._write_manifest()
            self.token_path(account.account_id).unlink(missing_ok=True)
            raise
        return account

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"accounts": [], "active_account_id": None}
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Could not read YouTube account profiles: {error}"
            ) from error
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("accounts"), list)
        ):
            raise RuntimeError("YouTube account profile file has an invalid format.")

        accounts = []
        seen_ids = set()
        for item in manifest["accounts"]:
            if not isinstance(item, dict):
                raise RuntimeError("YouTube account profile file has an invalid entry.")
            account_id = item.get("account_id")
            label = item.get("label")
            if (
                not isinstance(account_id, str)
                or not _ACCOUNT_ID_PATTERN.fullmatch(account_id)
                or account_id in seen_ids
                or not isinstance(label, str)
                or not label.strip()
                or len(label) > 60
            ):
                raise RuntimeError("YouTube account profile file has an invalid entry.")
            seen_ids.add(account_id)
            reauth_required = item.get("reauth_required", False)
            if not isinstance(reauth_required, bool):
                raise RuntimeError(
                    "YouTube account profile file has an invalid entry."
                )
            accounts.append(
                {
                    "account_id": account_id,
                    "label": label.strip(),
                    "reauth_required": reauth_required,
                }
            )
        active = manifest.get("active_account_id")
        if active is not None and active not in seen_ids:
            active = None
        return {"accounts": accounts, "active_account_id": active}

    def _write_manifest(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._manifest, indent=2).encode("utf-8")
        self._atomic_write(self.manifest_path, content)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, path)
            if os.name != "nt":
                path.chmod(0o600)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _find(self, account_id: str) -> dict[str, Any]:
        if not isinstance(account_id, str) or not _ACCOUNT_ID_PATTERN.fullmatch(
            account_id
        ):
            raise ValueError("Invalid YouTube account profile.")
        for item in self._manifest["accounts"]:
            if item["account_id"] == account_id:
                return item
        raise ValueError("YouTube account profile was not found.")

    def _contains(self, account_id: str | None) -> bool:
        return any(
            item["account_id"] == account_id
            for item in self._manifest["accounts"]
        )


def write_protected_token(path: Path, token_json: str) -> None:
    """Write OAuth token JSON encrypted with the current Windows user DPAPI."""
    raw = token_json.encode("utf-8")
    if os.name == "nt":
        raw = _TOKEN_HEADER + _dpapi_transform(raw, protect=True)
    YouTubeAccountStore._atomic_write(Path(path), raw)


def read_protected_token(path: Path) -> dict[str, Any]:
    """Read protected OAuth token JSON; accept a legacy plaintext token file."""
    try:
        raw = Path(path).read_bytes()
        if raw.startswith(_TOKEN_HEADER):
            if os.name != "nt":
                raise RuntimeError(
                    "This Google OAuth token is protected for Windows and cannot be "
                    "opened on this operating system."
                )
            raw = _dpapi_transform(raw[len(_TOKEN_HEADER):], protect=False)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read Google OAuth token: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError("Google OAuth token has an invalid format.")
    return data


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    input_buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(
        len(data),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if protect:
        transform = crypt32.CryptProtectData
        transform.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        transform.restype = wintypes.BOOL
        success = transform(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
    else:
        transform = crypt32.CryptUnprotectData
        transform.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        transform.restype = wintypes.BOOL
        success = transform(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output_blob.pbData)
