"""Strict lifecycle shapes cannot imply ingestion/retrieval quality."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.knowledge_models import (
    Chunk,
    DocumentVersion,
    IngestionJob,
    IngestionProgress,
    RetrievalResult,
    VectorHit,
)
from tests.knowledge_fixtures import chunk, knowledge_base, submission


@pytest.mark.parametrize(
    "change",
    [
        {"completed_units": -1},
        {"completed_units": True},
        {"completed_units": 2, "total_units": 1},
        {"completed_batches": ["b1", "b1"]},
        {"step": "done"},
        {"unexpected": 1},
    ],
)
def test_progress_rejects_invalid_and_duplicate_completion(change):
    data = {"step": "parse", "completed_units": 0, "total_units": None, "completed_batches": []}
    with pytest.raises(ValidationError):
        IngestionProgress.model_validate(data | change)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "processing"},
        {"status": "completed"},
        {"attempt_count": 1},
        {"attempt_count": True},
        {"lease_owner": "worker"},
        {"attempt_count": 4},
    ],
)
def test_job_cannot_forge_processing_or_history(change):
    job = submission(knowledge_base(uuid4()))[2]
    with pytest.raises(ValidationError):
        IngestionJob.model_validate(job.model_dump() | change)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "active"},
        {"status": "retired"},
        {"content_hash": "not-hash"},
        {"chunk_count": 10001},
    ],
)
def test_version_rejects_unpublished_active_and_invalid_hash(change):
    version = submission(knowledge_base(uuid4()))[1]
    with pytest.raises(ValidationError):
        DocumentVersion.model_validate(version.model_dump() | change)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), "0.5", True])
def test_vector_hit_requires_real_finite_scores(score):
    with pytest.raises(ValidationError):
        VectorHit(chunk_id="c1", dense_rank=1, sparse_rank=None, fused_score=score)


def test_knowledge_records_roundtrip_strict_json_and_default_private():
    kb = knowledge_base(uuid4())
    data = kb.model_dump()
    del data["data_classification"]
    assert type(kb).model_validate(data).data_classification == "private"
    for value in (kb, *submission(kb)):
        assert type(value).model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "change",
    [
        {"ordinal": True},
        {"location": {}},
        {"chunk_type": "snippet"},
        {"metadata": {"score": float("nan")}},
    ],
)
def test_chunk_rejects_invalid_location_identity_and_nonfinite_metadata(change):
    record = chunk(submission(knowledge_base(uuid4()))[1])
    with pytest.raises(ValidationError):
        Chunk.model_validate(record.model_dump() | change)


def test_retrieval_result_has_full_text_location_and_typed_provenance_trace():
    kb = knowledge_base(uuid4())
    _, version, _ = submission(kb)
    record = chunk(version)
    result = RetrievalResult(
        kb_id=kb.kb_id,
        document_id=version.document_id,
        document_version_id=version.document_version_id,
        chunk_id=record.chunk_id,
        content="Actual returned body, not an embedding anchor",
        score=0.5,
        location=record.location,
        source_metadata={
            "title": "Fixture",
            "filename": "fixture.pdf",
            "year": 2026,
            "data_classification": "private",
            "content_hash": record.content_hash,
        },
        retrieval_trace={
            "dense_rank": 1,
            "sparse_rank": None,
            "fused_score": 0.01,
            "rerank_score": None,
            "index_version": kb.index_version,
            "degraded": True,
        },
    )
    assert RetrievalResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate(result.model_dump() | {"content": ""})
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate(
            result.model_dump() | {"retrieval_trace": {"arbitrary": "metadata"}}
        )
