"""Original integrity and range checks, without pretending a live PDF was parsed."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from domain.documents import FetchedDocument, ParsedDocument
from domain.research.agents.coverage import section_coverage
from domain.research.agents.originals import evidence_from_original, register_original
from domain.research.facts import Claim, ClaimEvidenceLink, SectionPlan
from domain.research.search import SearchResult


def original(*, table=False, pdf=False, location=None):
    digest = hashlib.sha256(b"original bytes").hexdigest()
    parsed = ParsedDocument.model_validate(
        {
            "input_hash": digest,
            "parser_version": "test-v1",
            "blocks": [
                {
                    "type": "table" if table else "text",
                    "content": "Method | Accuracy (%)\nA | 0"
                    if table
                    else "Original method uses a frozen protocol.",
                    "location": location
                    or ({"page_start": 2} if pdf else {"line_start": 1, "line_end": 2}),
                    "caption": "Table 1: held-out test" if table else None,
                    "notes": ["* Values use the same split."] if table else [],
                }
            ],
        }
    )
    body = json.dumps(
        parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    parsed_hash = hashlib.sha256(body).hexdigest()
    media = "application/pdf" if pdf else "text/html"
    fetched = FetchedDocument.model_validate(
        {
            "content_ref": {
                "key": f"original/{digest}",
                "sha256": digest,
                "size": 14,
                "media_type": media,
            },
            "parsed_content_ref": {
                "key": f"parsed/{parsed_hash}",
                "sha256": parsed_hash,
                "size": len(body),
                "media_type": "application/json",
            },
            "final_url": "https://arxiv.org/pdf/1706.03762v7"
            if pdf
            else "https://EXAMPLE.org:443/doc#fragment",
            "media_type": media,
            "hash": digest,
            "locations": [block.location for block in parsed.blocks],
            "parser_version": parsed.parser_version,
        }
    )
    candidate = SearchResult(
        source_id="provider-id",
        source_type="paper" if pdf else "web",
        title="Candidate title",
        snippet="Search summary not in original",
        url="https://example.org/abstract",
        source_tier="peer_reviewed" if pdf else "unknown",
    )
    source = register_original(candidate, fetched, parsed, retrieved_at=datetime.now(UTC))
    return candidate, fetched, parsed, source


def extract(fetched, parsed, source, **kwargs):
    return evidence_from_original(
        source,
        fetched,
        parsed,
        block_index=kwargs.pop("block_index", 0),
        evidence_type=kwargs.pop("evidence_type", "method"),
        **kwargs,
    )


def test_search_snippet_never_becomes_original_evidence():
    candidate, fetched, parsed, source = original()
    with pytest.raises(ValueError, match="exact original"):
        extract(fetched, parsed, source, quote=candidate.snippet)
    evidence = extract(fetched, parsed, source, quote="uses a frozen protocol")
    assert evidence.location.line_start == 1
    assert evidence.content_hash == fetched.hash
    assert evidence.source_id == source.source_id
    assert evidence.extraction_method == "parser:test-v1:verified_range"


@pytest.mark.parametrize("field,value", [("parser_version", "forged"), ("input_hash", "f" * 64)])
def test_mutated_parser_handoff_rejected(field, value):
    _, fetched, parsed, source = original()
    changed = parsed.model_copy(update={field: value})
    with pytest.raises(ValueError, match="integrity"):
        extract(fetched, changed, source, quote=parsed.blocks[0].content)


def test_mutated_body_with_same_input_hash_rejected():
    _, fetched, parsed, source = original()
    changed = parsed.model_copy(
        update={"blocks": [parsed.blocks[0].model_copy(update={"content": "Invented conclusion"})]}
    )
    with pytest.raises(ValueError, match="integrity"):
        extract(fetched, changed, source, quote="Invented conclusion")


@pytest.mark.parametrize("index", [-1, 1, True, "0"])
def test_block_index_is_strict_and_bounded(index):
    _, fetched, parsed, source = original()
    with pytest.raises(ValueError, match="block index"):
        extract(fetched, parsed, source, block_index=index, quote=parsed.blocks[0].content)


def test_table_is_atomic_with_headers_units_caption_and_notes():
    _, fetched, parsed, source = original(table=True)
    block = parsed.blocks[0]
    for clipped in ("A | 0", block.content, block.content + "\n" + block.caption):
        with pytest.raises(ValueError, match="entire block"):
            extract(fetched, parsed, source, quote=clipped, evidence_type="result_table")
    entire = "\n".join([block.content, block.caption, *block.notes])
    evidence = extract(fetched, parsed, source, quote=entire, evidence_type="result_table")
    assert "Accuracy (%)" in evidence.quote_or_raw_content
    assert "A | 0" in evidence.quote_or_raw_content
    assert block.notes[0] in evidence.quote_or_raw_content


def test_text_cannot_be_relabelled_as_result_table():
    _, fetched, parsed, source = original()
    with pytest.raises(ValueError, match="parsed table"):
        extract(
            fetched, parsed, source, quote=parsed.blocks[0].content, evidence_type="result_table"
        )


def test_arxiv_candidate_is_not_automatically_peer_reviewed():
    _, _, _, source = original(pdf=True)
    assert source.source_tier == "primary"


@pytest.mark.parametrize(
    "pdf,location", [(False, {"page_start": 1}), (True, {"section": "Invented instead of a page"})]
)
def test_media_location_mismatch_rejected_even_with_valid_parser_hash(pdf, location):
    with pytest.raises(ValueError, match="page"):
        original(pdf=pdf, location=location)


def test_versioned_arxiv_identity_merges_mirror_not_different_versions():
    candidate, fetched, parsed, source = original(pdf=True)
    mirrored = register_original(
        candidate,
        fetched.model_copy(update={"final_url": "https://export.arxiv.org/pdf/1706.03762v7.pdf"}),
        parsed,
        retrieved_at=datetime.now(UTC),
    )
    changed = register_original(
        candidate,
        fetched.model_copy(update={"final_url": "https://arxiv.org/pdf/1706.03762v8"}),
        parsed,
        retrieved_at=datetime.now(UTC),
    )
    assert mirrored.source_id == source.source_id
    assert changed.source_id != source.source_id


def test_source_id_does_not_depend_on_title_provider_or_url_fragment():
    candidate, fetched, parsed, source = original()
    changed = candidate.model_copy(
        update={"title": "Another title", "source_id": "another-provider"}
    )
    second = register_original(
        changed,
        fetched.model_copy(update={"final_url": "https://example.org/doc#other"}),
        parsed,
        retrieved_at=datetime.now(UTC),
    )
    assert source.source_id == second.source_id
    assert source.canonical_url == "https://example.org/doc"
    quote = parsed.blocks[0].content
    assert (
        extract(fetched, parsed, source, quote=quote).evidence_id
        == extract(fetched, parsed, second, quote=quote).evidence_id
    )


def test_wrong_original_source_rejected():
    _, fetched, parsed, source = original()
    with pytest.raises(ValueError, match="Source does not"):
        extract(
            fetched,
            parsed,
            source.model_copy(update={"content_hash": "e" * 64}),
            quote=parsed.blocks[0].content,
        )


def coverage_inputs():
    _, fetched, parsed, source = original()
    evidence = extract(fetched, parsed, source, quote=parsed.blocks[0].content)
    plan = SectionPlan(
        section_id="section_1",
        title="Methods",
        objective="Verify protocol",
        claim_specs=[
            {
                "spec_id": "spec-1",
                "text": "Method is evaluated",
                "required_conditions": ["split"],
                "required_source_tiers": ["primary"],
            }
        ],
        sub_questions=["Which split?"],
        retrieval_anchors=[],
        evidence_requirements=[],
        analysis_requirements=[],
    )
    claim = Claim(
        claim_id="claim-1",
        spec_ids=["spec-1"],
        text="Method uses frozen split",
        claim_type="factual",
        conditions={"split": "held-out"},
        status="supported",
        status_reason="Model said so",
    )
    link = ClaimEvidenceLink(
        claim_id=claim.claim_id,
        evidence_id=evidence.evidence_id,
        relation="supports",
        rationale="Original method text",
    )
    return plan, claim, evidence, source, link


def test_spec_with_no_claim_still_has_gap():
    plan, _, _, _, _ = coverage_inputs()
    updates, coverage = section_coverage(plan, {}, {}, {}, [])
    assert not updates and not coverage.covered_claim_ids
    assert coverage.gaps[0].claim_spec_id == "spec-1"
    assert coverage.gaps[0].claim_id is None


@pytest.mark.parametrize("missing", ["link", "tier", "conditions"])
def test_model_supported_status_cannot_bypass_requirements(missing):
    plan, claim, evidence, source, link = coverage_inputs()
    if missing == "conditions":
        claim = claim.model_copy(update={"conditions": {}})
    if missing != "tier":
        source = source.model_copy(update={"source_tier": "primary"})
    updates, coverage = section_coverage(
        plan,
        {claim.claim_id: claim},
        {evidence.evidence_id: evidence},
        {source.source_id: source},
        [] if missing == "link" else [link],
    )
    assert updates[claim.claim_id].status == "insufficient"
    assert not coverage.covered_claim_ids and coverage.gaps


def test_support_refutation_conflict_remains_limited():
    plan, claim, evidence, source, link = coverage_inputs()
    source = source.model_copy(update={"source_tier": "primary"})
    updates, coverage = section_coverage(
        plan,
        {claim.claim_id: claim},
        {evidence.evidence_id: evidence},
        {source.source_id: source},
        [link, link.model_copy(update={"relation": "refutes"})],
    )
    assert updates[claim.claim_id].status == "limited"
    assert not coverage.covered_claim_ids and coverage.gaps


def test_qualified_original_support_covers_spec():
    plan, claim, evidence, source, link = coverage_inputs()
    source = source.model_copy(update={"source_tier": "primary"})
    updates, coverage = section_coverage(
        plan,
        {claim.claim_id: claim},
        {evidence.evidence_id: evidence},
        {source.source_id: source},
        [link],
    )
    assert updates[claim.claim_id].status == "supported"
    assert coverage.covered_claim_ids == [claim.claim_id]
    assert not coverage.gaps


def test_supported_claim_does_not_hide_another_uncovered_claim():
    plan, claim, evidence, source, link = coverage_inputs()
    source = source.model_copy(update={"source_tier": "primary"})
    unlinked = claim.model_copy(update={"claim_id": "unlinked"})
    _, coverage = section_coverage(
        plan,
        {claim.claim_id: claim, unlinked.claim_id: unlinked},
        {evidence.evidence_id: evidence},
        {source.source_id: source},
        [link],
    )
    assert coverage.covered_claim_ids == [claim.claim_id]
    assert [gap.claim_id for gap in coverage.gaps] == ["unlinked"]


def test_unknown_link_never_silently_disappears():
    plan, claim, evidence, source, link = coverage_inputs()
    with pytest.raises(ValueError, match="unknown reference"):
        section_coverage(
            plan,
            {claim.claim_id: claim},
            {evidence.evidence_id: evidence},
            {source.source_id: source},
            [link.model_copy(update={"evidence_id": "invented"})],
        )
