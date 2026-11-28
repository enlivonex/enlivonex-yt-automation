"""Tests for local Ollama story and metadata providers."""

from unittest.mock import Mock

import pytest

from app.ai.ollama import OllamaClient
from app.metadata.generator import OllamaMetadataGenerator
from app.story.provider import OllamaStoryProvider


class FakeOllamaClient:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate_json(self, system_prompt, user_prompt):
        self.prompts.append((system_prompt, user_prompt))
        return self.response


def test_ollama_client_posts_to_local_chat_api(monkeypatch):
    response = Mock()
    response.json.return_value = {"message": {"content": '{"title": "Local"}'}}
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(OllamaClient, "_ensure_server_and_model", Mock())
    monkeypatch.setattr("app.ai.ollama.requests.post", post)

    client = OllamaClient(model="qwen2.5:3b")
    result = client.generate_json("system", "user")

    assert result == {"title": "Local"}
    args, kwargs = post.call_args
    assert args[0] == "http://127.0.0.1:11434/api/chat"
    assert kwargs["json"]["model"] == "qwen2.5:3b"
    assert kwargs["json"]["stream"] is False
    assert kwargs["json"]["format"] == "json"
    assert kwargs["json"]["options"] == {"temperature": 0.7}
    assert kwargs["timeout"] == (5, 300)


def test_ollama_client_can_force_cpu_inference(monkeypatch):
    response = Mock()
    response.json.return_value = {"message": {"content": '{"title": "Local"}'}}
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(OllamaClient, "_ensure_server_and_model", Mock())
    monkeypatch.setattr("app.ai.ollama.requests.post", post)

    OllamaClient(cpu_only=True).generate_json("system", "user")

    assert post.call_args.kwargs["json"]["options"] == {
        "temperature": 0.7,
        "num_gpu": 0,
    }


def test_ollama_client_sends_response_schema(monkeypatch):
    schema = {"type": "object", "required": ["result"]}
    response = Mock()
    response.json.return_value = {"message": {"content": '{"result": "ok"}'}}
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(OllamaClient, "_ensure_server_and_model", Mock())
    monkeypatch.setattr("app.ai.ollama.requests.post", post)

    result = OllamaClient().generate_json("system", "user", response_schema=schema)

    assert result == {"result": "ok"}
    assert post.call_args.kwargs["json"]["format"] == schema


def test_ollama_client_generates_local_embedding(monkeypatch):
    response = Mock()
    response.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(OllamaClient, "_ensure_server_and_model", Mock())
    monkeypatch.setattr("app.ai.ollama.requests.post", post)

    vector = OllamaClient(model="nomic-embed-text").generate_embedding("health")

    assert vector == [0.1, 0.2, 0.3]
    assert post.call_args.args[0].endswith("/api/embed")
    assert post.call_args.kwargs["json"]["model"] == "nomic-embed-text"


def test_ollama_client_reports_unavailable_service(monkeypatch):
    import requests

    monkeypatch.setattr(
        "app.ai.ollama.requests.post",
        Mock(side_effect=requests.exceptions.ConnectionError()),
    )
    monkeypatch.setattr(OllamaClient, "_ensure_server_and_model", Mock())
    with pytest.raises(RuntimeError, match="Start Ollama"):
        OllamaClient().generate_json("system", "user")


def test_missing_model_does_not_trigger_unload_request(monkeypatch):
    client = OllamaClient(model="nomic-embed-text")
    monkeypatch.setattr(
        client,
        "_ensure_server_and_model",
        Mock(side_effect=RuntimeError("model is not installed")),
    )
    post = Mock()
    monkeypatch.setattr("app.ai.ollama.requests.post", post)

    with pytest.raises(RuntimeError, match="model is not installed"):
        client.generate_embedding("health")
    client.unload()

    post.assert_not_called()


def test_ollama_client_starts_local_server_when_not_running(monkeypatch):
    import requests

    healthy_response = Mock()
    healthy_response.raise_for_status.return_value = None
    get = Mock(
        side_effect=[
            requests.exceptions.ConnectionError("not ready"),
            requests.exceptions.ConnectionError("not ready"),
            healthy_response,
        ]
    )
    popen = Mock()
    monkeypatch.setattr("app.ai.ollama.requests.get", get)
    monkeypatch.setattr("app.ai.ollama.subprocess.Popen", popen)
    monkeypatch.setattr(
        OllamaClient, "_find_ollama_executable", staticmethod(lambda: "ollama")
    )
    monkeypatch.setattr("app.ai.ollama.time.sleep", Mock())

    OllamaClient()._ensure_server()

    popen.assert_called_once()
    assert popen.call_args.args[0] == ["ollama", "serve"]


def test_ollama_client_unloads_model_after_use(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(OllamaClient, "_ensure_server", Mock())
    monkeypatch.setattr("app.ai.ollama.requests.post", post)
    client = OllamaClient()
    client._model_requested = True

    client.unload()

    assert post.call_args.args[0].endswith("/api/generate")
    assert post.call_args.kwargs["json"]["keep_alive"] == 0
    assert client._model_requested is False


def test_ollama_story_provider_validates_and_parses_story():
    client = FakeOllamaClient(
        {
            "title": "A fresh idea",
            "hook": "What if?",
            "segments": [
                {
                    "text": "A short spoken story.",
                    "start_time": 0.0,
                    "end_time": 3.0,
                    "emphasis": "curious",
                }
            ],
            "total_duration": 20.0,
            "tone": "casual",
        }
    )

    story = OllamaStoryProvider(client=client).generate_story(
        "clip metadata", 20, constraints={"niche": "gaming"}
    )

    assert story.title == "A fresh idea"
    assert story.full_text == "A short spoken story."
    assert "unrelated background footage" in client.prompts[0][1]
    assert "selected topic is 'gaming'" in client.prompts[0][1]


def test_ollama_story_provider_rejects_malformed_story():
    provider = OllamaStoryProvider(client=FakeOllamaClient({"title": "No segments"}))
    with pytest.raises(RuntimeError, match="malformed story"):
        provider.generate_story("metadata", 20)


def test_ollama_metadata_provider_validates_and_limits_fields():
    client = FakeOllamaClient(
        {
            "title": "A" * 120,
            "description": "A short description.",
            "hashtags": ["#gaming", "shorts"] + [f"tag{i}" for i in range(10)],
        }
    )
    from app.story.models import Story, StorySegment

    story = Story(
        title="Story",
        hook="Hook",
        segments=[StorySegment("Text", 0.0, 1.0)],
        total_duration=1,
        tone="casual",
    )
    metadata = OllamaMetadataGenerator(client=client).generate_metadata(
        story, keywords=["health"]
    )

    assert len(metadata.title) == 100
    assert metadata.hashtags == ["gaming", "shorts"] + [f"tag{i}" for i in range(8)]
    assert metadata.visibility == "private"
    assert "health" in client.prompts[0][1]
    assert "Do not switch to a different topic" in client.prompts[0][0]


def test_ollama_metadata_provider_rejects_invalid_fields():
    from app.story.models import Story, StorySegment

    story = Story("Story", "Hook", [StorySegment("Text", 0, 1)], 1, "casual")
    provider = OllamaMetadataGenerator(client=FakeOllamaClient({"title": 123}))
    with pytest.raises(RuntimeError, match="malformed metadata"):
        provider.generate_metadata(story)


def test_ollama_metadata_provider_uses_story_defaults_for_missing_fields(caplog):
    from app.story.models import Story, StorySegment

    story = Story(
        "Known story title",
        "Known story hook",
        [StorySegment("Known narration text", 0, 1)],
        1,
        "casual",
    )
    provider = OllamaMetadataGenerator(
        client=FakeOllamaClient({})
    )

    metadata = provider.generate_metadata(story)

    assert metadata.title == "Known story title"
    assert metadata.description == "Known story hook"
    assert metadata.hashtags == ["shorts", "casual"]
    assert "using story-derived defaults" in caplog.text
