"""Web research and a SQLite-backed local RAG store using Ollama embeddings."""

from dataclasses import asdict, dataclass
import base64
import binascii
import json
import logging
import math
from pathlib import Path
import re
import time
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

import requests

from ..database.db import Database

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    def generate_embedding(self, text: str) -> list[float]:
        """Return a vector for a text input."""


@dataclass(frozen=True)
class ResearchSource:
    title: str
    url: str
    source_type: str


@dataclass(frozen=True)
class ResearchResult:
    topic: str
    context: str
    sources: list[ResearchSource]
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "context": self.context,
            "sources": [asdict(source) for source in self.sources],
            "warning": self.warning,
        }


class ResearchService:
    """Search the web, index user documents, and retrieve relevant evidence."""

    SUPPORTED_DOCUMENTS = {".txt", ".md", ".pdf"}
    MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
    MAX_WEB_RESULTS = 5
    MAX_RETRIEVED_CHUNKS = 4
    WEB_SEARCH_ATTEMPTS = 4
    WEB_SEARCH_FALLBACK_BACKEND = "mojeek,yahoo,startpage"

    def __init__(
        self,
        db: Database,
        embeddings: EmbeddingProvider | None = None,
        embedding_model: str = "default",
        web_enabled: bool = True,
    ):
        self.db = db
        self.embeddings = embeddings
        self.embedding_model = embedding_model.strip() or "default"
        self.web_enabled = web_enabled
        self._embedding_available = embeddings is not None
        self._embedding_warning = ""

    def ingest_directory(self, directory: Path) -> dict[str, int]:
        """Index supported text/Markdown/PDF files from a chosen folder."""
        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"Research documents folder not found: {directory}")

        files = sorted(
            path for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in self.SUPPORTED_DOCUMENTS
        )
        if not files:
            supported = ", ".join(sorted(self.SUPPORTED_DOCUMENTS))
            raise ValueError(
                f"No supported research documents found. Supported formats: {supported}."
            )

        total_chunks = 0
        for path in files:
            if path.stat().st_size > self.MAX_DOCUMENT_BYTES:
                raise ValueError(
                    f"Research document exceeds the 15 MB limit: {path.name}"
                )
            text = self._read_document(path)
            chunks = self._index_text(
                text=text,
                source_id=self._file_source_id(path),
                source_name=path.name,
                source_url=f"local://{quote(path.name)}",
                source_type="local",
                topic="",
            )
            total_chunks += chunks
        return {"files": len(files), "chunks": total_chunks}

    def research(self, topic: str) -> ResearchResult:
        """Gather current search snippets and retrieve matching indexed evidence."""
        topic = topic.strip()
        if not topic:
            raise ValueError("Research topic cannot be empty.")

        warning = ""
        web_sources: list[ResearchSource] = []
        web_context_parts: list[str] = []
        if self.web_enabled:
            try:
                web_results = self._search_web(topic)
                for result in web_results:
                    title = result["title"]
                    url = result["url"]
                    snippet = result["snippet"]
                    web_sources.append(ResearchSource(title, url, "web"))
                    web_context_parts.append(
                        f"Source: {title}\nURL: {url}\nEvidence: {snippet}"
                    )
                    self._index_text(
                        text=f"{title}\n{snippet}",
                        source_id=f"web:{url}",
                        source_name=title,
                        source_url=url,
                        source_type="web",
                        topic=topic,
                    )
            except (ImportError, RuntimeError, TimeoutError, OSError) as error:
                raise RuntimeError(
                    "Online research failed, so no script was generated. "
                    f"Check internet access and the web-search dependency: {error}"
                ) from error
            if not web_sources:
                raise RuntimeError(
                    "Online research returned no usable sources, so no script "
                    "was generated. Try a more specific topic or disable online "
                    "research to intentionally use offline mode."
                )
        query_vector = self._generate_embedding(topic)
        if self._embedding_warning and self._embedding_warning not in warning:
            warning = " ".join(
                part for part in (warning, self._embedding_warning) if part
            )
        retrieved = [
            item
            for item in self._retrieve(query_vector, topic)
            if item["source_type"] == "local"
        ]
        if not self.web_enabled:
            warning = " ".join(
                part
                for part in (
                    warning,
                    "Online web research is disabled. Generation uses local "
                    "documents and model knowledge, which may be outdated.",
                )
                if part
            )
        if not retrieved:
            if not web_context_parts:
                warning = " ".join(
                    part
                    for part in (
                        warning,
                        "No matching local research was found; generation will "
                        "use the selected topic without retrieved local evidence.",
                    )
                    if part
                )

        context_parts = web_context_parts
        source_map: dict[str, ResearchSource] = {}
        for source in web_sources:
            source_map[source.url] = source
        for item in retrieved:
            source = ResearchSource(
                title=item["source_name"],
                url=item["source_url"],
                source_type=item["source_type"],
            )
            source_map[source.url] = source
            context_parts.append(
                f"Source: {source.title}\nURL: {source.url}\n"
                f"Evidence: {item['content']}"
            )

        return ResearchResult(
            topic=topic,
            context="\n\n".join(context_parts),
            sources=list(source_map.values()),
            warning=warning,
        )

    def _search_web(self, topic: str) -> list[dict[str, str]]:
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException
        except ImportError as error:
            raise ImportError(
                "Install the optional web research dependency with "
                "`pip install -r requirements-ai.txt`."
            ) from error

        results: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for attempt in range(self.WEB_SEARCH_ATTEMPTS):
            backend = (
                "auto"
                if attempt % 2 == 0
                else self.WEB_SEARCH_FALLBACK_BACKEND
            )
            query = (
                topic
                if attempt < 2
                else f"{topic} reliable facts research"
            )
            try:
                with DDGS(timeout=5) as search:
                    results = search.text(
                        query,
                        max_results=self.MAX_WEB_RESULTS,
                        backend=backend,
                    )
                normalized = self._normalize_search_results(results)
                if normalized:
                    return normalized
                last_error = DDGSException(
                    "Search returned no usable results."
                )
            except (DDGSException, RuntimeError, TimeoutError, OSError) as error:
                last_error = error
            if attempt + 1 < self.WEB_SEARCH_ATTEMPTS:
                logger.warning(
                    "Web search attempt %s/%s failed: %s",
                    attempt + 1,
                    self.WEB_SEARCH_ATTEMPTS,
                    last_error,
                )
                time.sleep(min(attempt + 1, 2))

        try:
            results = self._search_bing(topic)
            if results:
                return results
        except (ImportError, OSError, requests.RequestException, ValueError) as error:
            logger.warning("Bing web search fallback failed: %s", error)
            last_error = error

        error = last_error or DDGSException("No results found.")
        raise RuntimeError(
            "Search provider failed after "
            f"{self.WEB_SEARCH_ATTEMPTS} attempts "
            f"({type(error).__name__}): {error}"
        ) from error

    @staticmethod
    def _normalize_search_results(results: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("href", item.get("url", ""))).strip()
            snippet = str(item.get("body", item.get("snippet", ""))).strip()
            title = re.split(
                r"\s+(?:-|\|)\s+YouTube", title, maxsplit=1, flags=re.IGNORECASE
            )[0].rstrip(" -|")
            title = title[:250]
            snippet = re.sub(r"\s+", " ", snippet)[:1000]
            if title and url.startswith(("https://", "http://")) and snippet:
                normalized.append(
                    {"title": title, "url": url, "snippet": snippet}
                )
        return normalized

    def _search_bing(self, topic: str) -> list[dict[str, str]]:
        from lxml import etree, html

        response = requests.get(
            "https://www.bing.com/search",
            params={"q": topic, "count": self.MAX_WEB_RESULTS},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        try:
            document = html.fromstring(response.content)
        except (etree.ParserError, ValueError):
            logger.warning("Bing search returned an empty or unreadable page.")
            return []
        results = []
        stop_words = {
            "about",
            "and",
            "but",
            "buy",
            "download",
            "for",
            "from",
            "game",
            "games",
            "get",
            "how",
            "into",
            "official",
            "play",
            "site",
            "the",
            "this",
            "that",
            "what",
            "when",
            "where",
            "with",
        }
        topic_terms = {
            token
            for token in re.findall(r"[a-z0-9]{3,}", topic.casefold())
            if token not in stop_words
        }
        minimum_matches = min(2, len(topic_terms))
        for node in document.xpath(
            '//li[contains(concat(" ", normalize-space(@class), " "), " b_algo ")]'
        ):
            links = node.xpath(".//h2/a[1]")
            snippets = node.xpath(
                './/div[contains(concat(" ", normalize-space(@class), " "), '
                '" b_caption ")]/p[1]'
            )
            if not links or not snippets:
                continue
            title = re.sub(r"\s+", " ", " ".join(links[0].itertext())).strip()
            snippet = re.sub(
                r"\s+", " ", " ".join(snippets[0].itertext())
            ).strip()
            snippet = re.sub(r"\s*Read more\s*$", "", snippet, flags=re.IGNORECASE)
            url = self._unwrap_bing_url(links[0].get("href", ""))
            hostname = (urlsplit(url).hostname or "").casefold()
            searchable_text = f"{title} {snippet}".casefold()
            matching_terms = sum(term in searchable_text for term in topic_terms)
            if (
                title
                and snippet
                and matching_terms >= minimum_matches
                and url.startswith(("https://", "http://"))
                and hostname != "bing.com"
                and not hostname.endswith(".bing.com")
            ):
                results.append(
                    {"title": title, "url": url, "snippet": snippet}
                )
                if len(results) >= self.MAX_WEB_RESULTS:
                    break
        return self._normalize_search_results(results)

    @staticmethod
    def _unwrap_bing_url(url: str) -> str:
        encoded = parse_qs(urlsplit(url).query).get("u", [""])[0]
        if not encoded.startswith("a1"):
            return url
        value = encoded[2:]
        value += "=" * (-len(value) % 4)
        try:
            decoded = base64.urlsafe_b64decode(value).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return url
        return decoded if decoded.startswith(("https://", "http://")) else url

    def _index_text(
        self,
        text: str,
        source_id: str,
        source_name: str,
        source_url: str,
        source_type: str,
        topic: str,
    ) -> int:
        chunks = self._chunk_text(text)
        if not chunks:
            return 0
        if self.db.conn is None:
            raise RuntimeError("Research database is not connected.")

        self.db.conn.execute(
            "DELETE FROM research_chunks WHERE source_id = ?", (source_id,)
        )
        indexed = 0
        for index, chunk in enumerate(chunks):
            vector = self._generate_embedding(chunk)
            stored_model = (
                self.embedding_model if vector is not None else "lexical"
            )
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO research_chunks (
                    source_id, source_name, source_url, source_type, topic,
                    chunk_index, content, embedding_json, embedding_model,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    source_id,
                    source_name,
                    source_url,
                    source_type,
                    topic,
                    index,
                    chunk,
                    json.dumps(vector or []),
                    stored_model,
                ),
            )
            indexed += 1
        self.db.conn.commit()
        return indexed

    def _retrieve(
        self, query_vector: list[float] | None, topic: str
    ) -> list[dict[str, str]]:
        if self.db.conn is None:
            raise RuntimeError("Research database is not connected.")
        if query_vector is None:
            rows = self.db.conn.execute(
                """
                SELECT source_name, source_url, source_type, content, embedding_json
                FROM research_chunks
                WHERE embedding_model = 'lexical'
                  AND (source_type = 'local' OR lower(trim(topic)) = lower(trim(?)))
                """,
                (topic,),
            ).fetchall()
            query_terms = set(self._search_terms(topic))
            ranked = []
            for row in rows:
                content_terms = set(self._search_terms(
                    f"{row['source_name']} {row['content']}"
                ))
                score = len(query_terms & content_terms)
                if score:
                    ranked.append((score, row))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            return [
                {
                    "source_name": row["source_name"],
                    "source_url": row["source_url"],
                    "source_type": row["source_type"],
                    "content": row["content"],
                }
                for score, row in ranked[: self.MAX_RETRIEVED_CHUNKS]
            ]

        rows = self.db.conn.execute(
            """
            SELECT source_name, source_url, source_type, content, embedding_json
            FROM research_chunks
            WHERE embedding_model = ?
              AND (source_type = 'local' OR lower(trim(topic)) = lower(trim(?)))
            """,
            (self.embedding_model, topic),
        ).fetchall()
        ranked = []
        for row in rows:
            try:
                vector = json.loads(row["embedding_json"])
                score = self._cosine_similarity(query_vector, vector)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            ranked.append((score, row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "source_name": row["source_name"],
                "source_url": row["source_url"],
                "source_type": row["source_type"],
                "content": row["content"],
            }
            for score, row in ranked[: self.MAX_RETRIEVED_CHUNKS]
            if score > 0
        ]

    def _generate_embedding(self, text: str) -> list[float] | None:
        if self.embeddings is None or not self._embedding_available:
            return None
        try:
            return self.embeddings.generate_embedding(text)
        except RuntimeError as error:
            self._embedding_available = False
            self._embedding_warning = (
                "Local semantic embeddings are unavailable; using offline "
                "keyword retrieval instead."
            )
            logger.warning(
                "Ollama embeddings unavailable; falling back to lexical retrieval: %s",
                error,
            )
            return None

    @staticmethod
    def _search_terms(text: str) -> list[str]:
        return [
            term
            for term in re.findall(r"[a-z0-9]{3,}", text.casefold())
            if term not in {"the", "and", "for", "with", "from", "about"}
        ]

    @staticmethod
    def _cosine_similarity(first: list[float], second: list[float]) -> float:
        if len(first) != len(second) or not first:
            return 0.0
        first_norm = math.sqrt(sum(value * value for value in first))
        second_norm = math.sqrt(sum(value * value for value in second))
        if not first_norm or not second_norm:
            return 0.0
        return sum(a * b for a, b in zip(first, second)) / (
            first_norm * second_norm
        )

    @staticmethod
    def _chunk_text(
        text: str, words_per_chunk: int = 140, overlap: int = 20
    ) -> list[str]:
        words = re.findall(r"\S+", text)
        if not words:
            return []
        chunks = []
        start = 0
        while start < len(words):
            end = min(len(words), start + words_per_chunk)
            chunks.append(" ".join(words[start:end]))
            if end == len(words):
                break
            start = end - overlap
        return chunks

    @staticmethod
    def _read_document(path: Path) -> str:
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as error:
                raise RuntimeError(
                    "PDF indexing requires pypdf. Install dependencies from "
                    "requirements-ai.txt."
                ) from error
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        else:
            text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise ValueError(f"Research document contains no extractable text: {path.name}")
        return text

    @staticmethod
    def _file_source_id(path: Path) -> str:
        return f"file:{path.resolve()}"
