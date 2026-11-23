"""Small HTTP client for a locally running Ollama instance."""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Protocol
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)
_SERVER_START_LOCK = threading.Lock()


class JSONGenerator(Protocol):
    """Interface shared by the Ollama client and test doubles."""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Generate a JSON object from the supplied prompts."""


class OllamaClient:
    """Generate JSON responses from a local Ollama chat model."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:3b",
        timeout_seconds: float = 300,
        server_start_timeout_seconds: float = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.server_start_timeout_seconds = server_start_timeout_seconds
        self._model_requested = False

    def list_installed_models(self) -> list[str]:
        """Return model names currently available in the local Ollama instance."""
        self._ensure_server()
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=(2, 8))
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as error:
            raise RuntimeError(
                f"Ollama at {self.base_url} did not return its model list: {error}"
            ) from error
        models = data.get("models", []) if isinstance(data, dict) else []
        names = []
        for model in models:
            if isinstance(model, dict) and isinstance(model.get("name"), str):
                names.append(model["name"])
        return names

    def pull_model(self, model: str | None = None) -> None:
        """Download a model into the local Ollama library."""
        target = (model or self.model).strip()
        if not target:
            raise RuntimeError("No Ollama model name was provided.")
        self._ensure_server()
        try:
            response = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": target, "stream": False},
                timeout=(5, max(self.timeout_seconds, 1800)),
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"Could not download Ollama model '{target}': {error}"
            ) from error

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Request and parse a JSON object from Ollama."""
        try:
            self._ensure_server_and_model()
            self._model_requested = True
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "format": response_schema or "json",
                    "keep_alive": "5m",
                    "options": {"temperature": 0.7},
                },
                timeout=(5, self.timeout_seconds),
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(
                "Could not reach Ollama at "
                f"{self.base_url}. Start Ollama and confirm the selected model "
                f"'{self.model}' is installed."
            ) from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError(
                f"Ollama did not respond within {self.timeout_seconds:g} seconds."
            ) from error
        except requests.exceptions.RequestException as error:
            raise RuntimeError(f"Ollama request failed: {error}") from error
        except ValueError as error:
            raise RuntimeError("Ollama returned an invalid HTTP response.") from error

        if not isinstance(result, dict):
            raise RuntimeError("Ollama returned an invalid response object.")
        message = result.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty response.")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Ollama returned malformed JSON.") from error
        if not isinstance(data, dict):
            raise RuntimeError("Ollama response must be a JSON object.")
        return data

    def generate_embedding(self, text: str) -> list[float]:
        """Create a local vector embedding for retrieval."""
        if not text.strip():
            raise ValueError("Embedding input cannot be empty.")
        try:
            self._ensure_server_and_model()
            self._model_requested = True
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": text,
                    "keep_alive": "5m",
                },
                timeout=(5, self.timeout_seconds),
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"Ollama embedding request failed for model '{self.model}': {error}"
            ) from error
        except ValueError as error:
            raise RuntimeError("Ollama returned invalid embedding data.") from error

        embeddings = result.get("embeddings") if isinstance(result, dict) else None
        if (
            not isinstance(embeddings, list)
            or not embeddings
            or not isinstance(embeddings[0], list)
            or not embeddings[0]
            or any(not isinstance(value, (float, int)) for value in embeddings[0])
        ):
            raise RuntimeError("Ollama returned malformed embedding data.")
        return [float(value) for value in embeddings[0]]

    def unload(self) -> None:
        """Ask Ollama to unload the model so it releases RAM/VRAM."""
        if not self._model_requested:
            return
        self._ensure_server()
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": "", "keep_alive": 0},
                timeout=(5, 30),
            )
            response.raise_for_status()
            self._model_requested = False
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"Could not unload Ollama model '{self.model}': {error}"
            ) from error

    def _ensure_server_and_model(self) -> None:
        self._ensure_server()
        try:
            response = requests.get(
                f"{self.base_url}/api/tags", timeout=(2, 5)
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as error:
            raise RuntimeError(
                f"Ollama at {self.base_url} did not return its model list: {error}"
            ) from error

        models = data.get("models", []) if isinstance(data, dict) else []
        installed = {
            model.get("name")
            for model in models
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
        if self.model not in installed:
            raise RuntimeError(
                f"Ollama model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )

    def _ensure_server(self) -> None:
        try:
            response = requests.get(
                f"{self.base_url}/api/tags", timeout=(1, 2)
            )
            response.raise_for_status()
            return
        except requests.exceptions.RequestException as error:
            if not self._is_local_endpoint():
                raise RuntimeError(
                    f"Could not reach Ollama at {self.base_url}: {error}"
                ) from error

        with _SERVER_START_LOCK:
            try:
                response = requests.get(
                    f"{self.base_url}/api/tags", timeout=(1, 2)
                )
                response.raise_for_status()
                return
            except requests.exceptions.RequestException:
                pass

            executable = self._find_ollama_executable()
            if not executable:
                raise RuntimeError(
                    "Ollama is not responding and its executable was not found. "
                    "Install Ollama or start the Ollama application, then retry."
                )

            startup_options = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                startup_options["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                )
            try:
                subprocess.Popen([executable, "serve"], **startup_options)
            except OSError as error:
                raise RuntimeError(f"Could not start Ollama service: {error}") from error

            deadline = time.monotonic() + self.server_start_timeout_seconds
            last_error = None
            while time.monotonic() < deadline:
                try:
                    response = requests.get(
                        f"{self.base_url}/api/tags", timeout=(1, 2)
                    )
                    response.raise_for_status()
                    logger.info("Started Ollama service at %s", self.base_url)
                    return
                except requests.exceptions.RequestException as error:
                    last_error = error
                    time.sleep(0.5)
            raise RuntimeError(
                "Ollama service did not start within "
                f"{self.server_start_timeout_seconds:g} seconds: {last_error}"
            )

    def _is_local_endpoint(self) -> bool:
        parsed = urlparse(self.base_url)
        return parsed.hostname in {"localhost", "127.0.0.1", "::1"}

    @staticmethod
    def _find_ollama_executable() -> str | None:
        executable = shutil.which("ollama")
        if executable:
            return executable
        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("PROGRAMFILES")
        if local_app_data:
            candidates.append(
                os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe")
            )
        if program_files:
            candidates.append(os.path.join(program_files, "Ollama", "ollama.exe"))
        return next((path for path in candidates if os.path.isfile(path)), None)
