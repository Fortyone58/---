"""Local policy embeddings and a replaceable Qdrant index; SQL stays authoritative."""

import atexit
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import config
from .services import now, stamp

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
INDEX_VERSION = 1
CHUNK_SIZE = 320
CHUNK_OVERLAP = 64
LOGGER = logging.getLogger(__name__)
_INDEXES = {}
_INDEXES_LOCK = threading.RLock()


class RagUnavailable(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Options:
    enabled: bool
    model: str
    directory: Path
    model_cache: Path
    min_score: float


def options():
    directory = Path(os.getenv("QINGHE_RAG_DIR", config.ROOT / "data/runtime/rag")).resolve()
    try:
        score = float(os.getenv("RAG_MIN_SCORE", "0.65"))
    except ValueError:
        score = 0.65
    return Options(os.getenv("RAG_ENABLED", "true").lower() == "true",
                   os.getenv("RAG_MODEL", MODEL_NAME), directory,
                   Path(os.getenv("RAG_MODEL_CACHE", directory / "models")).resolve(),
                   max(0.0, min(score, 1.0)))


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: int
    section_index: int
    start: int
    end: int
    text: str
    title: str
    location: str

    @property
    def embedding_text(self):
        return f"{self.title[:90]}\n{self.location[:45]}\n{self.text}"


def chunks_for_documents(documents):
    chunks = []
    for document in sorted(documents, key=lambda item: item.id):
        for section_index, section in enumerate(document.sections):
            text, start = section["text"], 0
            while start < len(text):
                end = min(start + CHUNK_SIZE, len(text))
                if end < len(text):
                    cuts = [text.rfind(mark, start + CHUNK_SIZE // 2, end)
                            for mark in ("\n", "\u3002", "\uff1b", "\uff01", "\uff1f")]
                    if max(cuts) >= 0:
                        end = max(cuts) + 1
                body = text[start:end]
                if body.strip():
                    key = json.dumps([document.id, section_index, start, end, document.title,
                                      section["location"], body], ensure_ascii=False)
                    identity = str(uuid5(NAMESPACE_URL, "qinghe-policy:" + key))
                    chunks.append(Chunk(identity, document.id, section_index, start, end,
                                        body, document.title, section["location"]))
                if end == len(text):
                    break
                start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def fingerprint(chunks, model):
    content = [INDEX_VERSION, model, [[chunk.id, chunk.embedding_text] for chunk in chunks]]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


class LocalIndex:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.client = None
        self.model = None
        self.last_error = None

    @property
    def manifest_path(self):
        return self.settings.directory / "manifest.json"

    @property
    def vectors_path(self):
        return self.settings.directory / "vectors"

    def manifest(self):
        try:
            with self.manifest_path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if data.get("version") == INDEX_VERSION and data.get("model") == self.settings.model:
                return data
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return {}

    def collection_problem(self, metadata):
        """Check the derived store before reporting it usable or querying it."""
        collection = metadata.get("collection")
        if not isinstance(collection, str) or not collection.startswith("policy_"):
            return "collection_missing"
        expected_chunks = metadata.get("chunks")
        if not isinstance(expected_chunks, int) or isinstance(expected_chunks, bool) or expected_chunks <= 0:
            return "collection_invalid"
        if not self.vectors_path.is_dir():
            return "collection_missing"
        try:
            client = self.storage()
            if not client.collection_exists(collection):
                return "collection_missing"
            info = client.get_collection(collection)
            vector_config = info.config.params.vectors
            dimension = getattr(vector_config, "size", None)
            if not isinstance(metadata.get("dimension"), int) or dimension != metadata["dimension"]:
                return "collection_invalid"
            try:
                point_count = client.count(collection_name=collection, exact=True)
            except TypeError:
                # Older qdrant-client releases accepted the collection positionally.
                point_count = client.count(collection, exact=True)
            actual_chunks = getattr(point_count, "count", None)
            if (not isinstance(actual_chunks, int) or isinstance(actual_chunks, bool) or
                    actual_chunks != expected_chunks):
                return "collection_count_mismatch"
        except RagUnavailable as error:
            return error.reason
        except Exception:
            return "collection_unavailable"
        return None

    def status(self, chunks=None):
        metadata = self.manifest()
        cached = self.settings.model_cache.is_dir() and any(self.settings.model_cache.rglob("*.onnx"))
        collection_problem = None
        if self.settings.enabled and metadata.get("chunks") and cached:
            collection_problem = self.collection_problem(metadata)
        reason = self.last_error or collection_problem
        ready = bool(metadata.get("chunks") and cached and not reason)
        stale = bool(metadata and chunks is not None and
                     metadata.get("fingerprint") != fingerprint(chunks, self.settings.model))
        state = "disabled" if not self.settings.enabled else "unavailable" if reason else (
            "stale" if stale else "ready" if ready else "not_ready")
        return {"enabled": self.settings.enabled, "ready": state == "ready", "state": state,
                "model": self.settings.model, "model_cached": bool(cached), "store": "qdrant_local",
                "dimension": metadata.get("dimension"), "documents": metadata.get("documents", 0),
                "chunks": metadata.get("chunks", 0), "built_at": metadata.get("built_at"),
                "fingerprint": metadata.get("fingerprint"), "min_score": self.settings.min_score,
                "reason": reason, "rebuild_required": state == "unavailable" and bool(metadata)}

    def load_model(self, download=False):
        if self.model is None:
            try:
                from fastembed import TextEmbedding
                self.model = TextEmbedding(model_name=self.settings.model,
                                           cache_dir=str(self.settings.model_cache), threads=2,
                                           providers=["CPUExecutionProvider"], local_files_only=not download)
            except Exception as error:
                raise RagUnavailable("model_unavailable") from error
        return self.model

    def storage(self):
        if self.client is None:
            try:
                from qdrant_client import QdrantClient
                self.settings.directory.mkdir(parents=True, exist_ok=True)
                self.client = QdrantClient(path=str(self.vectors_path))
            except Exception as error:
                raise RagUnavailable("storage_unavailable") from error
        return self.client

    def write_manifest(self, metadata):
        descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=self.settings.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False)
            os.replace(temporary, self.manifest_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def refresh(self, chunks, force=False, download=False):
        if not self.settings.enabled:
            raise RagUnavailable("disabled")
        with self.lock:
            try:
                return self._refresh(chunks, force, download)
            except RagUnavailable as error:
                self.last_error = error.reason
                raise

    def _refresh(self, chunks, force, download):
        from qdrant_client import models

        current = self.manifest()
        digest = fingerprint(chunks, self.settings.model)
        if not force and current.get("fingerprint") == digest:
            problem = self.collection_problem(current)
            if problem:
                raise RagUnavailable(problem)
            self.last_error = None
            return current
        if not chunks:
            raise RagUnavailable("empty_knowledge")
        client = self.storage()
        started = time.monotonic()
        model = self.load_model(download)
        collection = None
        try:
            vectors = list(model.passage_embed([chunk.embedding_text for chunk in chunks], batch_size=16))
            if len(vectors) != len(chunks) or not vectors or len(vectors[0]) == 0:
                raise ValueError("Invalid embedding batch")
            dimension = len(vectors[0])
            collection = "policy_" + uuid4().hex
            client.create_collection(collection, vectors_config=models.VectorParams(
                size=dimension, distance=models.Distance.COSINE))
            client.upsert(collection, points=[models.PointStruct(
                id=chunk.id, vector=vector.tolist(), payload={"document_id": chunk.document_id})
                for chunk, vector in zip(chunks, vectors, strict=True)], wait=True)
            metadata = {"version": INDEX_VERSION, "model": self.settings.model, "dimension": dimension,
                        "collection": collection, "fingerprint": digest, "built_at": stamp(now()),
                        "documents": len({chunk.document_id for chunk in chunks}), "chunks": len(chunks),
                        "build_seconds": round(time.monotonic() - started, 3)}
            self.write_manifest(metadata)
        except Exception as error:
            if collection:
                try:
                    client.delete_collection(collection)
                except Exception:
                    LOGGER.warning("Could not remove an incomplete derived policy index")
            raise RagUnavailable("index_unavailable") from error
        self.last_error = None
        old_collection = current.get("collection")
        if old_collection and old_collection != collection and old_collection.startswith("policy_"):
            try:
                client.delete_collection(old_collection)
            except Exception:
                LOGGER.warning("Could not remove an obsolete derived policy index")
        return metadata

    def search(self, chunks, allowed_ids, query, limit=20):
        if not allowed_ids:
            return [], self.status(chunks)
        with self.lock:
            try:
                metadata = self.refresh(chunks)
                model = self.load_model()
                from qdrant_client import models
                vector = next(model.query_embed(query)).tolist()
                result = self.storage().query_points(
                    collection_name=metadata["collection"], query=vector,
                    query_filter=models.Filter(must=[models.HasIdCondition(has_id=list(allowed_ids))]),
                    limit=limit, score_threshold=self.settings.min_score, with_payload=False)
                self.last_error = None
                # Enforce current SQL eligibility again, even if storage returned an unexpected ID.
                return [(str(point.id), float(point.score)) for point in result.points
                        if str(point.id) in allowed_ids], metadata
            except RagUnavailable as error:
                self.last_error = error.reason
                raise
            except Exception as error:
                self.last_error = "search_unavailable"
                raise RagUnavailable(self.last_error) from error

    def close(self):
        with self.lock:
            if self.client is not None:
                self.client.close()
                self.client = None
            self.model = None


def index():
    settings = options()
    key = (settings.directory, settings.model_cache, settings.model, settings.min_score)
    with _INDEXES_LOCK:
        instance = _INDEXES.get(key)
        if instance is None or instance.settings.enabled != settings.enabled:
            if instance:
                instance.close()
            instance = _INDEXES[key] = LocalIndex(settings)
        return instance


def close_indexes():
    with _INDEXES_LOCK:
        for instance in _INDEXES.values():
            instance.close()
        _INDEXES.clear()


def status(chunks=None):
    return index().status(chunks)


def rebuild(documents, download=False):
    instance = index()
    chunks = chunks_for_documents(documents)
    instance.refresh(chunks, force=True, download=download)
    return instance.status(chunks)


def hybrid_search(documents, eligible_ids, query, lexical_score, limit=5):
    started = time.monotonic()
    chunks = chunks_for_documents(documents)
    eligible = [chunk for chunk in chunks if chunk.document_id in eligible_ids]
    # Topic/article constraints belong to the authoritative caller, not the vector store.
    scores = {chunk.id: lexical_score(chunk) for chunk in eligible}
    eligible = [chunk for chunk in eligible if scores[chunk.id] >= 0]
    lexical = sorted([chunk for chunk in eligible if scores[chunk.id] > 0],
                     key=lambda chunk: (-scores[chunk.id], chunk.document_id, chunk.section_index, chunk.start))
    semantic, metadata, degraded = [], {}, None
    if options().enabled and eligible:
        try:
            semantic, metadata = index().search(chunks, {chunk.id for chunk in eligible}, query)
        except RagUnavailable as error:
            degraded = error.reason
    by_id = {chunk.id: chunk for chunk in eligible}
    keyword_ranks = {chunk.id: rank for rank, chunk in enumerate(lexical, 1)}
    semantic_ranks = {identity: rank for rank, (identity, _score) in enumerate(semantic, 1)}
    semantic_scores = dict(semantic)
    combined = set(keyword_ranks) | set(semantic_ranks)

    def fusion(identity):
        return sum(1 / (60 + ranks[identity]) for ranks in (keyword_ranks, semantic_ranks) if identity in ranks)

    ordered = sorted(combined, key=lambda identity: (-fusion(identity),
                     -semantic_scores.get(identity, 0), by_id[identity].document_id,
                     by_id[identity].section_index, by_id[identity].start))
    selected, seen_sections = [], set()
    for identity in ordered:
        chunk = by_id[identity]
        section_key = (chunk.document_id, chunk.section_index)
        if section_key in seen_sections:
            continue
        seen_sections.add(section_key)
        selected.append({"chunk": chunk, "semantic_score": semantic_scores.get(identity),
                         "method": "hybrid" if identity in keyword_ranks and identity in semantic_ranks else
                         "semantic" if identity in semantic_ranks else "keyword"})
        if len(selected) >= limit:
            break
    enabled = options().enabled
    mode = "keyword_fallback" if degraded else "hybrid" if enabled and eligible else "keyword"
    return selected, {"mode": mode, "embedding_model": options().model if enabled else None,
                      "store": "qdrant_local" if enabled else None, "candidate_chunks": len(eligible),
                      "semantic_matches": len(semantic), "index_fingerprint": metadata.get("fingerprint"),
                      "degraded_reason": degraded, "elapsed_ms": round((time.monotonic() - started) * 1000)}


atexit.register(close_indexes)
