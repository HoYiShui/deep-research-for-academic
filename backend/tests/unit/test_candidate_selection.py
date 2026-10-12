"""Search selection keeps original-source diversity and rejects weak mirrors."""

from types import SimpleNamespace

from application.phase_workers import _oa_alternative, _preview_only, _same_work_original
from domain.research.candidate_selection import candidate_identity, select_candidates
from domain.research.ids import canonical_hash
from domain.research.search import SearchBatch, SearchOutcome, SearchResult


def candidate(url, title, *, source_type="web", fulltext_url=None):
    return SearchResult(
        source_id=url,
        source_type=source_type,
        title=title,
        snippet="",
        url=url,
        fulltext_url=fulltext_url,
    )


def test_doi_mirrors_deduplicate_and_official_source_outranks_aggregation():
    official = candidate(
        "https://kilthub.cmu.edu/articles/dataset/Insider_Threat_Test_Dataset/12841247",
        "Insider Threat Test Dataset",
    )
    mirrored = candidate(
        "https://doi.org/10.1109/SPW.2013.37?utm_source=search",
        "Bridging the Gap",
    )
    paper = candidate(
        "https://doi.org/10.1109/SPW.2013.37",
        "Bridging the Gap",
        source_type="paper",
        fulltext_url="https://university.edu/bridging.pdf",
    )
    aggregate = candidate(
        "https://emergentmind.com/topics/cert-insider-threat-dataset",
        "CERT Insider Threat Dataset Overview",
    )
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(
                source="web", attempts=1, status="ok", items=[aggregate, official, mirrored]
            ),
            SearchOutcome(source="openalex", attempts=1, status="ok", items=[paper]),
        ]
    )
    selected, rejected = select_candidates(batch, "Insider Threat Test Dataset")
    assert selected[0] == official
    assert paper not in selected and mirrored not in selected
    assert aggregate not in selected
    assert rejected == 3

    paper = paper.model_copy(update={"authors_or_publisher": ["Glasser", "Lindauer"]})
    mirrored = mirrored.model_copy(update={"authors_or_publisher": ["Glasser", "Lindauer"]})
    matching = SearchBatch(
        outcomes=[
            SearchOutcome(source="web", attempts=1, status="ok", items=[mirrored]),
            SearchOutcome(source="openalex", attempts=1, status="ok", items=[paper]),
        ]
    )
    selected, rejected = select_candidates(matching, "Glasser Lindauer 2013 Bridging the Gap")
    assert sum(candidate_identity(item) == candidate_identity(paper) for item in selected) == 1
    assert rejected == 1


def test_bibliography_directory_and_document_mirror_are_not_originals():
    bibliography = candidate(
        "https://researchr.org/publication/LiuWZJXM19/references", "Log2vec references"
    )
    mirror = candidate("https://www.scribd.com/document/1234/Log2vec", "Log2vec PDF")
    original = candidate("https://arxiv.org/pdf/1907.11112", "Log2vec original")
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(
                source="web", attempts=1, status="ok", items=[bibliography, mirror, original]
            )
        ]
    )
    selected, rejected = select_candidates(batch, "Log2vec")
    assert selected == [original]
    assert rejected == 2


def test_seen_original_and_provider_cap_do_not_block_other_provider():
    prior = candidate("https://example.org/paper?utm_source=a", "Known paper")
    web = [prior, candidate("https://example.org/other", "Other document")]
    paper = candidate(
        "https://doi.org/10.1145/3319535.3363224",
        "Log2vec",
        source_type="paper",
        fulltext_url="https://authors.edu/log2vec.pdf",
    )
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(source="web", attempts=1, status="ok", items=web),
            SearchOutcome(source="openalex", attempts=1, status="ok", items=[paper]),
        ]
    )
    selected, rejected = select_candidates(
        batch, "paper", seen={"https://example.org/paper"}, per_provider=1
    )
    assert prior not in selected
    assert paper in selected and len(selected) == 2
    assert rejected == 1


def test_arxiv_abstract_and_versioned_pdf_share_one_candidate_identity():
    abstract = candidate(
        "https://arxiv.org/abs/1910.03171",
        "Insider Threat Detection via Hierarchical Neural Temporal Point Processes",
    )
    paper = candidate(
        "https://arxiv.org/abs/1910.03171v2",
        abstract.title,
        source_type="paper",
        fulltext_url="https://arxiv.org/pdf/1910.03171v2",
    )
    assert candidate_identity(abstract) == candidate_identity(paper) == "arxiv:1910.03171"
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(source="web", attempts=1, status="ok", items=[abstract]),
            SearchOutcome(source="arxiv", attempts=1, status="ok", items=[paper]),
        ]
    )
    selected, rejected = select_candidates(batch, '"Insider Threat Detection"')
    assert selected == [paper]
    assert rejected == 1


def test_specific_author_query_rejects_generic_financial_news():
    original = candidate(
        "https://sei.cmu.edu/library/bridging-the-gap-a-pragmatic-approach",
        "Bridging the Gap: A Pragmatic Approach to Generating Insider Threat Data",
    ).model_copy(update={"authors_or_publisher": ["Joshua Glasser", "Brian Lindauer"]})
    news = candidate(
        "https://markets.businessinsider.com/news/stocks/engineering-supercycle",
        "Engineering Supercycle: Bridging the Autonomous Action Gap",
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[news, original])]
    )
    selected, rejected = select_candidates(
        batch, "Glasser Lindauer 2013 Bridging the Gap insider threat data"
    )
    assert selected == [original]
    assert rejected == 1


def test_bibliography_and_abstract_indexes_do_not_displace_originals():
    original = candidate(
        "https://arxiv.org/pdf/1910.03171",
        "Insider Threat Detection via Hierarchical Neural Temporal Point Processes",
    )
    indexes = [
        candidate(
            "https://www.scirp.org/reference/referencespapers?referenceid=42", original.title
        ),
        candidate("https://bibbase.org/network/publication/example", original.title),
        candidate("https://www.semanticscholar.org/paper/example", original.title),
    ]
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[*indexes, original])]
    )
    selected, rejected = select_candidates(
        batch,
        'Yuan 2019 "Insider Threat Detection via Hierarchical Neural Temporal Point Processes"',
    )
    assert selected == [original]
    assert rejected == 3


def test_paper_aggregation_page_is_excluded():
    aggregate = candidate(
        "https://papertik.app/p/W2761599262",
        "Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams",
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[aggregate])]
    )
    selected, rejected = select_candidates(
        batch,
        'Tuor 2017 "Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams"',
    )
    assert selected == []
    assert rejected == 1


def test_retryable_fetch_can_use_another_returned_url_for_same_arxiv_work():
    paper = candidate(
        "https://arxiv.org/abs/1910.03171v2",
        "Insider Threat Detection via Hierarchical Neural Temporal Point Processes",
        source_type="paper",
        fulltext_url="https://arxiv.org/pdf/1910.03171v2",
    )
    alternate = candidate("https://arxiv.org/pdf/1910.03171", paper.title)
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(source="arxiv", attempts=1, status="ok", items=[paper]),
            SearchOutcome(source="web", attempts=1, status="ok", items=[alternate]),
        ]
    )
    assert _same_work_original(paper, batch, {canonical_hash(paper)}) == alternate
    assert (
        _same_work_original(paper, batch, {canonical_hash(paper), canonical_hash(alternate)})
        is None
    )


def test_site_anchor_never_accepts_same_name_company_on_another_host():
    official = candidate(
        "https://kilthub.cmu.edu/articles/dataset/Insider_Threat_Test_Dataset/12841247",
        "Insider Threat Test Dataset",
    )
    namesake = candidate("https://www.sei.com/", "SEI business consulting")
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[namesake, official])]
    )
    selected, rejected = select_candidates(
        batch, 'site:kilthub.cmu.edu "Insider Threat Test Dataset" 12841247'
    )
    assert selected == [official]
    assert rejected == 1


def test_same_title_mirrors_do_not_consume_two_fetch_slots():
    first = candidate(
        "https://journal.example.org/review", "The Cert Dataset Decade: A Systematic Review"
    )
    mirror = candidate(
        "https://archive.example.org/review", "The Cert Dataset Decade: A Systematic Review"
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[first, mirror])]
    )
    selected, rejected = select_candidates(batch, '"Cert Dataset Decade" systematic review')
    assert selected == [first]
    assert rejected == 1


def test_quoted_paper_title_rejects_bibliography_page_with_only_snippet_match():
    original = candidate(
        "https://authors.edu/log2vec.pdf", "Log2vec: A Heterogeneous Graph Embedding Based Approach"
    )
    bibliography = candidate(
        "https://ccs.example.org/accepted-papers",
        "Accepted Papers – ACM CCS 2019",
    ).model_copy(update={"snippet": "Log2vec: A Heterogeneous Graph Embedding Based Approach"})
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(source="web", attempts=1, status="ok", items=[bibliography, original])
        ]
    )
    selected, rejected = select_candidates(batch, 'Liu 2019 "Log2vec" CCS')
    assert selected == [original]
    assert rejected == 1


def test_pdf_with_short_display_title_can_match_exact_title_in_excerpt():
    pdf = candidate(
        "https://ieee-security.org/TC/SPW2013/papers/data/5017a098.pdf",
        "A Pragmatic Approach to Generating Insider Threat Data",
    ).model_copy(
        update={
            "snippet": "Bridging the Gap: A Pragmatic Approach to Generating Insider Threat Data"
        }
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[pdf])]
    )
    selected, rejected = select_candidates(
        batch,
        'Glasser Lindauer 2013 "Bridging the Gap: A Pragmatic Approach to Generating Insider Threat Data"',
    )
    assert selected == [pdf]
    assert rejected == 0


def test_arxiv_pdf_without_dot_pdf_can_match_full_title_in_excerpt():
    pdf = candidate(
        "https://arxiv.org/pdf/1910.03171",
        "Insider Threat Detection via Hierarchical Neural Temporal ...",
    ).model_copy(
        update={
            "snippet": "Insider Threat Detection via Hierarchical Neural Temporal Point Processes"
        }
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[pdf])]
    )
    selected, rejected = select_candidates(
        batch,
        'Yuan 2019 "Insider Threat Detection via Hierarchical Neural Temporal Point Processes"',
    )
    assert selected == [pdf]
    assert rejected == 0


def test_pdf_that_only_cites_requested_work_is_not_its_original():
    thesis = candidate(
        "https://university.example.edu/thesis.pdf",
        "Detection of Insider Threats Based on Deep Learning Using User Behavior",
    ).model_copy(
        update={
            "snippet": "Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams, Tuor et al. 2017"
        }
    )
    batch = SearchBatch(
        outcomes=[SearchOutcome(source="web", attempts=1, status="ok", items=[thesis])]
    )
    selected, rejected = select_candidates(
        batch,
        'Tuor 2017 "Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams"',
    )
    assert selected == []
    assert rejected == 1


def test_short_publisher_preview_uses_matching_openalex_original():
    preview = candidate(
        "https://www.sciencedirect.com/science/article/abs/pii/123",
        "Insider Threat Detection with Temporal Point Processes",
    )
    oa = candidate(
        "https://doi.org/10.1109/example.2019.1",
        "Insider Threat Detection with Temporal Point Processes",
        source_type="paper",
        fulltext_url="https://authors.edu/temporal.pdf",
    ).model_copy(update={"provider": "openalex"})
    batch = SearchBatch(
        outcomes=[
            SearchOutcome(source="web", attempts=1, status="ok", items=[preview]),
            SearchOutcome(source="openalex", attempts=1, status="ok", items=[oa]),
        ]
    )
    fetched = SimpleNamespace(media_type="text/html")
    parsed = SimpleNamespace(blocks=[SimpleNamespace(content="Abstract only. Purchase access.")])
    assert _preview_only(preview, fetched, parsed)
    assert _oa_alternative(preview, batch, set()) == oa
