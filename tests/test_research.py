"""Tests for topic research and local retrieval."""

import sqlite3

import pytest

from app.database.db import Database
from app.research.service import ResearchService


class FakeEmbeddings:
    def generate_embedding(self, text: str) -> list[float]:
        lowered = text.casefold()
        if "health" in lowered:
            return [1.0, 0.0]
        if "gaming" in lowered:
            return [0.8, 0.6]
        return [0.0, 1.0]


def test_research_combines_web_sources_and_imported_local_documents(tmp_path):
    db = Database(tmp_path / "research.db")
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "health-notes.txt").write_text(
        "Health research notes discuss sleep, movement, and hydration.",
        encoding="utf-8",
    )
    with db:
        db.initialize()
        service = ResearchService(db, FakeEmbeddings())
        indexed = service.ingest_directory(docs)
        service._search_web = lambda _topic: [
            {
                "title": "Public health guide",
                "url": "https://example.org/health",
                "snippet": "Health guidance emphasizes sleep and regular movement.",
            }
        ]

        result = service.research("health")

    assert indexed == {"files": 1, "chunks": 1}
    assert "sleep" in result.context
    assert {source.source_type for source in result.sources} == {"local", "web"}
    assert "https://example.org/health" in {
        source.url for source in result.sources
    }
    assert any(source.url == "local://health-notes.txt" for source in result.sources)
    assert str(docs) not in result.context


def test_research_requires_evidence_if_web_and_local_sources_are_unavailable(
    tmp_path,
):
    db = Database(tmp_path / "research.db")
    with db:
        db.initialize()
        service = ResearchService(db, FakeEmbeddings())
        service._search_web = lambda _topic: []

        result = service.research("health")

    assert not result.context
    assert "No matching local research" in result.warning


def test_research_does_not_retrieve_web_sources_from_another_topic(tmp_path):
    db = Database(tmp_path / "research.db")
    with db:
        db.initialize()
        service = ResearchService(db, FakeEmbeddings())
        service._index_text(
            text="Gaming source content.",
            source_id="web:https://example.org/gaming",
            source_name="Gaming page",
            source_url="https://example.org/gaming",
            source_type="web",
            topic="gaming",
        )
        service._search_web = lambda _topic: []

        result = service.research("health")

    assert not result.context
    assert not result.sources


def test_research_retrieval_uses_matching_embedding_model(tmp_path):
    db = Database(tmp_path / "research.db")
    with db:
        db.initialize()
        old_model_service = ResearchService(
            db, FakeEmbeddings(), embedding_model="old-model"
        )
        old_model_service._index_text(
            text="Health source content.",
            source_id="web:https://example.org/health",
            source_name="Health page",
            source_url="https://example.org/health",
            source_type="web",
            topic="health",
        )

        new_model_service = ResearchService(
            db, FakeEmbeddings(), embedding_model="new-model"
        )
        new_model_service._search_web = lambda _topic: []

        result = new_model_service.research("health")

    assert not result.context
    assert not result.sources


def test_offline_research_uses_local_documents_without_embeddings(tmp_path):
    db = Database(tmp_path / "research.db")
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "health-notes.txt").write_text(
        "Health research covers sleep, movement, and hydration.",
        encoding="utf-8",
    )
    with db:
        db.initialize()
        service = ResearchService(
            db, embeddings=None, web_enabled=False
        )
        service._search_web = lambda _topic: pytest.fail(
            "offline research must not call web search"
        )

        indexed = service.ingest_directory(docs)
        result = service.research("health")

    assert indexed == {"files": 1, "chunks": 1}
    assert "sleep" in result.context
    assert [source.source_type for source in result.sources] == ["local"]


def test_research_falls_back_when_embedding_model_is_missing(tmp_path):
    class MissingEmbeddings:
        def generate_embedding(self, _text: str) -> list[float]:
            raise RuntimeError("embedding model is not installed")

    db = Database(tmp_path / "research.db")
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "health-notes.txt").write_text(
        "Health research covers sleep and hydration.",
        encoding="utf-8",
    )
    with db:
        db.initialize()
        service = ResearchService(
            db, MissingEmbeddings(), web_enabled=False
        )
        service.ingest_directory(docs)
        result = service.research("health")

    assert "sleep" in result.context
    assert "keyword retrieval" in result.warning


def test_database_migrates_research_chunks_without_embedding_model(tmp_path):
    database_path = tmp_path / "legacy-research.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE research_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_id, chunk_index)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO research_chunks (
            source_id, source_name, source_url, source_type, topic,
            chunk_index, content, embedding_json, created_at
        ) VALUES ('old', 'Old source', 'local://old.txt', 'local', '', 0,
                  'Existing content', '[1.0]', '2026-10-22')
        """
    )
    connection.commit()
    connection.close()

    with Database(database_path) as db:
        db.initialize()
        row = db.conn.execute(
            "SELECT embedding_model FROM research_chunks WHERE source_id = 'old'"
        ).fetchone()

    assert row["embedding_model"] == "default"


def test_chunks_have_overlap_to_preserve_context():
    text = " ".join(f"word{index}" for index in range(300))

    chunks = ResearchService._chunk_text(text, words_per_chunk=100, overlap=10)

    assert len(chunks) == 4
    assert chunks[0].split()[-10:] == chunks[1].split()[:10]
