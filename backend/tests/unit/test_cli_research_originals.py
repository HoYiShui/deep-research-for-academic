"""Read-only snapshot original binding, not persisted Run resume/cache behavior."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from application.errors import AppError
from cli.research_tools import ResearchDebugTools
from domain.documents import FetchedDocument
from domain.ports import AdapterError
from domain.research.agents.originals import evidence_from_original, register_original
from domain.research.phase_contracts import merge_phase_result
from domain.research.state import PipelineState
from tests.unit.test_phase_contracts import plans, result
from tests.unit.test_scout_originals import original
from tests.unit.test_state import initial_state


def fixture():
    candidate, fetched, parsed, source = original()
    old_key = f"research-content/{uuid4()}/{fetched.hash}"
    source = source.model_copy(update={"content_object_key": old_key})
    data = fetched.model_dump()
    data["content_ref"]["key"] = f"research-content/{uuid4()}/{fetched.hash}"
    fetched = FetchedDocument.model_validate(data)
    reads = []

    async def get(key):
        reads.append(key)

        async def stream():
            yield b"original bytes"

        return stream()

    tools = ResearchDebugTools(None, None, {}, fake=True, sources={source.source_id: source})
    tools.store = SimpleNamespace(get=get)
    return tools, candidate, fetched, parsed, source, reads


async def test_snapshot_reference_merges_without_rewriting_old_facts_or_run_metadata():
    tools, candidate, fetched, parsed, source, reads = fixture()
    state = PipelineState.model_validate(
        initial_state().model_dump()
        | {"phase": "research", "section_plans": plans(), "sources": {source.source_id: source}}
    )
    before = state.model_dump_json()
    fresh_source = register_original(candidate, fetched, parsed, retrieved_at=datetime.now(UTC))
    with pytest.raises(ValueError):
        merge_phase_result(state, result(state, {"sources": {source.source_id: fresh_source}}))
    bound = await tools._retain_original_reference(candidate, fetched, parsed)
    updated = register_original(candidate, bound, parsed, retrieved_at=datetime.now(UTC))
    merged = merge_phase_result(state, result(state, {"sources": {source.source_id: updated}}))
    assert merged.sources[source.source_id].content_object_key == source.content_object_key
    assert merged.run_metadata == state.run_metadata
    assert state.model_dump_json() == before
    assert bound.parsed_content_ref == fetched.parsed_content_ref
    assert fetched.content_ref.key != bound.content_ref.key
    assert reads == [source.content_object_key]
    evidence_from_original(
        updated,
        bound,
        parsed,
        block_index=0,
        quote="uses a frozen protocol",
        evidence_type="method",
    )


@pytest.mark.parametrize("body", [b"corrupt bytes!", b"original bytes plus", b"original byte"])
async def test_corrupted_snapshot_original_is_not_rebound(body):
    tools, candidate, fetched, parsed, _source, _reads = fixture()

    async def get(key):
        async def stream():
            yield body

        return stream()

    tools.store = SimpleNamespace(get=get)
    with pytest.raises(AdapterError, match="integrity"):
        await tools._retain_original_reference(candidate, fetched, parsed)


@pytest.mark.parametrize(
    "change", [{"data_classification": "private"}, {"content_object_key": "arbitrary/path"}]
)
async def test_nonpublic_or_malformed_reference_rejected_before_storage_read(change):
    tools, candidate, fetched, parsed, source, reads = fixture()
    tools.sources[source.source_id] = source.model_copy(update=change)
    with pytest.raises((AppError, ValueError)):
        await tools._retain_original_reference(candidate, fetched, parsed)
    assert reads == []


async def test_new_or_changed_content_keeps_independent_scope_without_reading_old_object():
    tools, candidate, fetched, parsed, source, reads = fixture()
    tools.sources[source.source_id] = source.model_copy(update={"content_hash": "f" * 64})
    assert await tools._retain_original_reference(candidate, fetched, parsed) == fetched
    tools.sources.clear()
    assert await tools._retain_original_reference(candidate, fetched, parsed) == fetched
    assert reads == []
