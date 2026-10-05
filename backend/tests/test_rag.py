from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import func, select

from app import rag
from app.agent import _policy_numbers_supported
from app.models import ChatLog, PolicyDoc
from app.policy import retrieve_policy
from app.services import now


class FixtureEmbedding:
    def __init__(self):
        self.batches = 0

    def passage_embed(self, texts, **kwargs):
        self.batches += 1
        for text in texts:
            yield np.array([1.0, 0.0, 0.0] if "POLICY_A" in text else [0.0, 1.0, 0.0])

    def query_embed(self, query):
        yield np.array([1.0, 0.0, 0.0])


@pytest.fixture
def vectors(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_MIN_SCORE", "0.65")
    model = FixtureEmbedding()
    rag.index().model = model
    model_file = tmp_path / "rag/models/fixture.onnx"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.touch()
    return model


def document(db, **overrides):
    values = {"title": "Semantic test policy", "publisher": "Fixture", "source_url": "https://example.org/rag",
              "version": "fixture", "verified": True, "verified_at": now(), "imported_at": now(),
              "verification_note": "Isolated test only", "sections": [
                  {"location": "Original paragraph", "text": "POLICY_A original evidence about student work."}]}
    values.update(overrides)
    row = PolicyDoc(**values)
    db.add(row)
    db.flush()
    return row


def test_semantic_query_has_exact_original_and_does_not_write_chat(factory, vectors):
    with factory.begin() as db:
        doc = document(db)
        before = db.scalar(select(func.count()).select_from(ChatLog))
        result = retrieve_policy(db, "Paraphrase absent from the original")
        hit = result["citations"][0]
        assert result["retrieval"]["mode"] == "hybrid"
        assert hit["retrieval_method"] == "semantic"
        assert hit["source"]["id"] == doc.id and hit["location"] == "Original paragraph"
        assert hit["text"] == doc.sections[0]["text"][hit["chunk"]["start"]:hit["chunk"]["end"]]
        assert db.scalar(select(func.count()).select_from(ChatLog)) == before


def test_scope_time_and_revocation_filter_before_vectors(factory, vectors):
    with factory.begin() as db:
        school = document(db, is_school_policy=True, source_key="S98")
        document(db, verified=False)
        document(db, usage_scope="archive_only", current_answer_allowed=False, source_key="S99")
        document(db, effective_from=now().date() + timedelta(days=1))
        result = retrieve_policy(db, "\u672c\u6821 paraphrase")
        assert [hit["source"]["id"] for hit in result["citations"]] == [school.id]
        national = retrieve_policy(db, "\u5168\u56fd paraphrase")
        assert not national["citations"]
        archived = retrieve_policy(db, "S99\u5386\u53f2\u539f\u6587")
        assert archived["citations"][0]["source"]["source_key"] == "S99"
        school.verified = False
        db.flush()
        assert not retrieve_policy(db, "\u672c\u6821 paraphrase")["citations"]


def test_school_pay_gap_cannot_be_filled_by_semantic_similarity(factory, vectors):
    with factory.begin() as db:
        document(db, is_school_policy=True)
        result = retrieve_policy(db, "\u672c\u6821\u65f6\u85aa\u662f\u591a\u5c11\uff1f")
        assert not result["citations"]


def test_explicit_source_reference_survives_semantic_ranking(factory, vectors):
    with factory.begin() as db:
        doc = document(db, source_key="S98", sections=[{"location": "Reference", "text": "Exact source original."}])
        result = retrieve_policy(db, "S98")
        assert result["citations"][0]["source"]["id"] == doc.id


def test_domain_heading_does_not_duplicate_embedding_context(factory, vectors, monkeypatch):
    captured = []
    original = vectors.query_embed

    def capture_query(query):
        captured.append(query)
        return original(query)

    monkeypatch.setattr(vectors, "query_embed", capture_query)
    with factory.begin() as db:
        document(db)
        retrieve_policy(db, "\u6821\u56ed\u52e4\u5de5\u52a9\u5b66\uff1a paraphrase")
    assert captured == ["\u6821\u56ed\u52e4\u5de5\u52a9\u5b66\u653f\u7b56\uff1aparaphrase"]


def test_index_updates_after_policy_changes_and_reuses_unchanged_content(factory, vectors):
    with factory.begin() as db:
        doc = document(db)
        first = retrieve_policy(db, "First paraphrase")
        first_batches = vectors.batches
        same = retrieve_policy(db, "Another paraphrase")
        assert vectors.batches == first_batches
        assert same["retrieval"]["index_fingerprint"] == first["retrieval"]["index_fingerprint"]
        doc.sections = [{"location": "Updated paragraph", "text": "POLICY_A changed verified original."}]
        db.flush()
        updated = retrieve_policy(db, "Another paraphrase")
        assert vectors.batches == first_batches + 1
        assert updated["retrieval"]["index_fingerprint"] != first["retrieval"]["index_fingerprint"]
        assert updated["citations"][0]["text"] == doc.sections[0]["text"]


def test_failed_rebuild_keeps_previous_index_and_uses_keyword_fallback(factory, vectors, monkeypatch):
    with factory.begin() as db:
        doc = document(db)
        retrieve_policy(db, "First paraphrase")
        old = rag.index().manifest()
        doc.sections = [{"location": "Updated", "text": "POLICY_A updated."}]
        db.flush()

        def failed_batch(*args, **kwargs):
            raise RuntimeError("Fixture failure")

        monkeypatch.setattr(vectors, "passage_embed", failed_batch)
        result = retrieve_policy(db, "\u6bcf\u5468\u80fd\u505a\u591a\u4e45\uff1f")
        assert result["retrieval"]["mode"] == "keyword_fallback"
        assert result["citations"][0]["location"] == "\u7b2c\u4e8c\u5341\u4e00\u6761"
        assert rag.index().manifest() == old


def test_explicit_article_numbers_keep_exact_lookup(factory, vectors):
    with factory.begin() as db:
        document(db)
        result = retrieve_policy(db, "\u7b2c21\u6761")
        assert result["retrieval"]["mode"] == "article_exact"
        assert [hit["location"] for hit in result["citations"]] == ["\u7b2c\u4e8c\u5341\u4e00\u6761"]
        assert vectors.batches == 0


def test_reciprocal_fusion_keeps_keyword_and_semantic_candidates(factory, vectors):
    with factory.begin() as db:
        semantic = document(db)
        keyword = document(db, source_url="https://example.org/keyword", sections=[
            {"location": "Keyword paragraph", "text": "needle refers to an exact policy term."}])
        result = retrieve_policy(db, "needle")
        assert {hit["source"]["id"] for hit in result["citations"]} == {semantic.id, keyword.id}
        assert {hit["retrieval_method"] for hit in result["citations"]} == {"semantic", "keyword"}


def test_admin_index_controls_and_empty_or_disabled_states(client, headers):
    for username in ("student", "library", "aid"):
        assert client.get("/api/admin/rag/status", headers=headers(username)).status_code == 403
        assert client.post("/api/admin/rag/rebuild", headers=headers(username)).status_code == 403
    status = client.get("/api/admin/rag/status", headers=headers("admin_demo"))
    assert status.status_code == 200 and status.json()["state"] == "disabled"
    assert client.post("/api/admin/rag/rebuild", headers=headers("admin_demo")).status_code == 503


def test_admin_rebuild_persists_then_reopens_index(client, headers, vectors):
    rebuilt = client.post("/api/admin/rag/rebuild", headers=headers("admin_demo"))
    assert rebuilt.status_code == 200 and rebuilt.json()["ready"]
    before = rebuilt.json()["fingerprint"]
    collection = rag.index().manifest()["collection"]
    rag.close_indexes()
    assert client.get("/api/admin/rag/status", headers=headers("admin_demo")).json()["fingerprint"] == before
    assert rag.index().storage().count(collection).count == rebuilt.json()["chunks"]


def test_missing_qdrant_collection_is_unavailable_and_uses_keyword_fallback(factory, vectors):
    with factory.begin() as db:
        doc = document(db)
        retrieve_policy(db, "POLICY_A")
        instance = rag.index()
        instance.storage().delete_collection(instance.manifest()["collection"])
        status = rag.status(rag.chunks_for_documents([doc]))
        assert status["state"] == "unavailable"
        assert status["reason"] == "collection_missing"
        assert status["rebuild_required"] is True
        result = retrieve_policy(db, "POLICY_A")
        assert result["retrieval"]["mode"] == "keyword_fallback"
        assert result["retrieval"]["degraded_reason"] == "collection_missing"
        assert result["citations"][0]["text"] == doc.sections[0]["text"]


def test_unopenable_qdrant_collection_is_unavailable_and_uses_keyword_fallback(factory, vectors):
    class BrokenClient:
        def collection_exists(self, _collection):
            raise RuntimeError("fixture storage damaged")

        def close(self):
            pass

    with factory.begin() as db:
        document(db)
        retrieve_policy(db, "POLICY_A")
        rag.index().client = BrokenClient()
        status = rag.status()
        assert status["state"] == "unavailable"
        assert status["reason"] == "collection_unavailable"
        result = retrieve_policy(db, "POLICY_A")
        assert result["retrieval"]["mode"] == "keyword_fallback"
        assert result["retrieval"]["degraded_reason"] == "collection_unavailable"


def test_chunks_cover_original_and_remain_stable():
    from types import SimpleNamespace
    text = "POLICY_A " + "original sentence. " * 100
    doc = SimpleNamespace(id=101, title="Long document", sections=[{"location": "Paragraph", "text": text}])
    chunks = rag.chunks_for_documents([doc])
    assert len(chunks) > 1 and chunks[-1].end == len(text)
    assert all(chunk.text == text[chunk.start:chunk.end] for chunk in chunks)
    assert all(current.start <= previous.end for previous, current in zip(chunks, chunks[1:]))
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in rag.chunks_for_documents([doc])]


@pytest.mark.parametrize("answer", [
    "\u6bcf\u5468\u6700\u591a40\u5c0f\u65f6\u3002[1]",
    "40\u5c0f\u65f6/\u5468\u3002[1]",
    "\u6bcf\u5468\u81f3\u5c11\u5de5\u4f5c8\u5c0f\u65f6\u3002[1]",
    "\u6bcf\u5468\u6700\u591a8\u5c0f\u65f6\uff0c\u5426\u5219\u53d6\u6d88\u5b66\u7c4d\u3002[1]",
])
def test_policy_period_comparison_and_unsupported_sanctions(answer):
    result = {"citations": [{"text": "\u6bcf\u5468\u4e0d\u8d85\u8fc78\u5c0f\u65f6\uff0c\u6bcf\u6708\u4e0d\u8d85\u8fc740\u5c0f\u65f6\u3002"}]}
    assert not _policy_numbers_supported(answer, result)
    assert _policy_numbers_supported("\u6bcf\u5468\u6700\u591a8\u5c0f\u65f6\uff0c\u6bcf\u6708\u6700\u591a40\u5c0f\u65f6\u3002[1]", result)


def test_cited_non_numeric_policy_answer_is_not_treated_as_grounded():
    result = {"citations": [{"text": "Students may receive training before starting work."}]}
    assert not _policy_numbers_supported("You can receive training before starting work. [1]", result)
