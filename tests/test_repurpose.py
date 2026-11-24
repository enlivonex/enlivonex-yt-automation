"""Tests for local video repurposing backend primitives."""

import hashlib
import json
from pathlib import Path
from concurrent.futures import CancelledError
from threading import Event
from types import SimpleNamespace

import pytest

from app.repurpose.models import TranscriptSegment, TranscriptWord
from app.repurpose.moments import OllamaMomentRanker
from app.repurpose.service import LocalClipAnalyzer
from app.repurpose.source import LocalVideoCatalog
from app.repurpose.transcription import FasterWhisperTranscriber
from app.utils.ffmpeg import VideoInfo


class FakeFFmpeg:
    def __init__(self, invalid_paths=()):
        self.invalid_paths = {Path(path) for path in invalid_paths}
        self.probed = []

    def probe(self, path):
        self.probed.append(path)
        if path in self.invalid_paths:
            raise RuntimeError("could not probe")
        return VideoInfo(120, 1920, 1080, 30, "h264", None, "16:9")


def test_catalog_inspects_supported_files_and_hashes_without_copying(tmp_path):
    source = tmp_path / "source.mp4"
    contents = b"small synthetic video fixture"
    source.write_bytes(contents)
    ffmpeg = FakeFFmpeg()

    result = LocalVideoCatalog(ffmpeg).scan(tmp_path)

    assert len(result.sources) == 1
    assert result.failures == ()
    assert result.sources[0].path == source.resolve()
    assert result.sources[0].sha256 == hashlib.sha256(contents).hexdigest()
    assert result.sources[0].file_size_bytes == len(contents)
    assert source.read_bytes() == contents
    assert ffmpeg.probed == [source.resolve()]


def test_catalog_reports_bad_video_without_hiding_other_files(tmp_path):
    good = tmp_path / "good.mp4"
    bad = tmp_path / "bad.mkv"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")
    ffmpeg = FakeFFmpeg(invalid_paths={bad.resolve()})

    result = LocalVideoCatalog(ffmpeg).scan(tmp_path)

    assert [source.path.name for source in result.sources] == ["good.mp4"]
    assert [(failure.path.name, failure.message) for failure in result.failures] == [
        ("bad.mkv", "could not probe")
    ]


def test_catalog_requires_existing_folder_and_rejects_unsupported_file(tmp_path):
    catalog = LocalVideoCatalog(FakeFFmpeg())
    with pytest.raises(FileNotFoundError):
        catalog.scan(tmp_path / "missing")

    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not a video", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported video format"):
        catalog.inspect(unsupported)


def test_catalog_stops_inspecting_when_cancellation_is_requested(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    ffmpeg = FakeFFmpeg()
    cancel_event = Event()
    cancel_event.set()

    with pytest.raises(CancelledError):
        LocalVideoCatalog(ffmpeg).inspect(source, cancel_event=cancel_event)

    assert ffmpeg.probed == []


def test_ranker_returns_no_suggestions_when_video_is_shorter_than_minimum():
    transcript = (TranscriptSegment(0, 5, "A short source."),)

    class FakeJSONGenerator:
        def generate_json(self, _system_prompt, _user_prompt, **_kwargs):
            raise AssertionError("The model should not be called for an empty window set.")

    ranked = OllamaMomentRanker(FakeJSONGenerator()).rank(
        transcript,
        source_duration=5,
        min_duration=15,
        max_duration=60,
    )

    assert ranked == ()


def test_ranker_bounds_local_model_prompt_for_long_transcripts():
    transcript = tuple(
        TranscriptSegment(start, start + 5, "A self-contained sentence.")
        for start in range(0, 1000, 5)
    )
    payload_sizes = []

    class FakeJSONGenerator:
        def generate_json(self, _system_prompt, user_prompt, **_kwargs):
            payload = json.loads(user_prompt)
            payload_sizes.append(len(payload["candidates"]))
            return {
                "candidates": [
                    {"id": 0, "score": 0.8, "rationale": "A complete idea."}
                ]
            }

    ranked = OllamaMomentRanker(FakeJSONGenerator()).rank(
        transcript,
        source_duration=1000,
        min_duration=15,
        max_duration=30,
    )

    assert payload_sizes == [32]
    assert len(ranked) == 1


def test_ranker_rejects_transcript_segments_outside_source_duration():
    transcript = (TranscriptSegment(0, 20, "Exceeds source duration."),)

    class FakeJSONGenerator:
        def generate_json(self, _system_prompt, _user_prompt, **_kwargs):
            raise AssertionError("Invalid transcript must be rejected before inference.")

    with pytest.raises(ValueError, match="exceeds source duration"):
        OllamaMomentRanker(FakeJSONGenerator()).rank(
            transcript, source_duration=10, min_duration=5, max_duration=10
        )


def test_ranker_uses_local_model_scores_for_generated_ranges():
    transcript = (
        TranscriptSegment(0, 10, "A complete thought starts."),
        TranscriptSegment(10, 20, "It ends with a useful idea."),
        TranscriptSegment(20, 30, "A second moment follows."),
        TranscriptSegment(30, 40, "It has a clear ending."),
        TranscriptSegment(40, 50, "A third thought starts."),
        TranscriptSegment(50, 60, "And reaches its conclusion."),
    )
    prompts = []

    class FakeJSONGenerator:
        def generate_json(
            self, system_prompt, user_prompt, *, response_schema=None
        ):
            prompts.append(
                (system_prompt, json.loads(user_prompt), response_schema)
            )
            return {
                "candidates": [
                    {"id": 1, "score": 0.9, "rationale": "A complete idea."},
                    {"id": 0, "score": 0.5, "rationale": "Has context."},
                ]
            }

    ranked = OllamaMomentRanker(FakeJSONGenerator()).rank(
        transcript,
        source_duration=60,
        min_duration=15,
        max_duration=30,
        max_candidates=2,
    )

    assert len(prompts) == 1
    assert ranked[0].score == 0.9
    assert ranked[0].start_seconds < ranked[0].end_seconds <= 60
    assert ranked[1].score == 0.5
    assert prompts[0][2]["properties"]["candidates"]["items"]["required"] == [
        "id",
        "score",
        "rationale",
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": [{"id": 50, "score": 0.5, "rationale": "Invalid id"}]},
        {"candidates": [{"id": 0, "score": 1.5, "rationale": "Invalid score"}]},
        {"candidates": [{"id": 0, "score": 0.5, "rationale": ""}]},
    ],
)
def test_ranker_rejects_malformed_local_model_output(response):
    transcript = tuple(
        TranscriptSegment(start, start + 10, "A transcript sentence.")
        for start in range(0, 60, 10)
    )

    class FakeJSONGenerator:
        def generate_json(self, _system_prompt, _user_prompt, **_kwargs):
            return response

    with pytest.raises(RuntimeError):
        OllamaMomentRanker(FakeJSONGenerator()).rank(
            transcript, 60, 15, 30
        )


def test_analyzer_connects_source_transcription_and_ranking(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source")
    transcript = (TranscriptSegment(0, 10, "A complete sentence."),)
    calls = []
    progress = []

    class FakeTranscriber:
        def transcribe(self, video_path, language=None):
            calls.append(("transcribe", video_path, language))
            return transcript

    class FakeRanker:
        def rank(self, actual_transcript, duration, minimum, maximum, count):
            calls.append(
                ("rank", actual_transcript, duration, minimum, maximum, count)
            )
            return ()

    analyzer = LocalClipAnalyzer(
        LocalVideoCatalog(FakeFFmpeg()), FakeTranscriber(), FakeRanker()
    )
    result = analyzer.analyze(
        path,
        min_duration=10,
        max_duration=30,
        max_candidates=3,
        language="en",
        progress_callback=progress.append,
    )

    assert result.source.path == path.resolve()
    assert result.transcript == transcript
    assert result.candidates == ()
    assert calls == [
        ("transcribe", path.resolve(), "en"),
        ("rank", transcript, 120, 10, 30, 3),
    ]
    assert progress == [
        "Inspecting source video...",
        "Transcribing with the local speech model...",
        "Ranking moments with the local Ollama model...",
    ]


def test_faster_whisper_requires_an_installed_local_model(tmp_path):
    with pytest.raises(FileNotFoundError, match="Install the model"):
        FasterWhisperTranscriber(tmp_path / "missing-model")


def test_faster_whisper_loads_model_in_local_files_only_mode(
    tmp_path, monkeypatch
):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    constructor_args = []
    transcribe_options = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            constructor_args.append((args, kwargs))

        def transcribe(self, path, **kwargs):
            transcribe_options.append(kwargs)
            return (
                iter(
                    [
                        SimpleNamespace(
                            start=0.1,
                            end=2.2,
                            text=" Spoken words. ",
                            words=[
                                SimpleNamespace(
                                    start=0.2, end=0.7, word=" Spoken"
                                ),
                                SimpleNamespace(
                                    start=0.8, end=1.5, word=" words."
                                ),
                            ],
                        )
                    ]
                ),
                SimpleNamespace(language="en"),
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    transcriber = FasterWhisperTranscriber(model_dir)

    result = transcriber.transcribe(source, language="en")

    assert constructor_args[0][1]["local_files_only"] is True
    assert transcribe_options[0]["word_timestamps"] is True
    assert result == (
        TranscriptSegment(
            0.1,
            2.2,
            "Spoken words.",
            (
                TranscriptWord(0.2, 0.7, "Spoken"),
                TranscriptWord(0.8, 1.5, "words."),
            ),
        ),
    )


def test_faster_whisper_stops_between_transcript_segments(
    tmp_path, monkeypatch
):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    cancel_event = Event()

    def segments():
        yield SimpleNamespace(start=0, end=1, text="First segment.")
        cancel_event.set()
        yield SimpleNamespace(start=1, end=2, text="Second segment.")

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, _path, **_kwargs):
            return iter(segments()), SimpleNamespace(language="en")

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    transcriber = FasterWhisperTranscriber(model_dir)

    with pytest.raises(CancelledError):
        transcriber.transcribe(source, cancel_event=cancel_event)
