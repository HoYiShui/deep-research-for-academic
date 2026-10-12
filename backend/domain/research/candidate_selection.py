"""Bounded, source-aware selection of search candidates before original fetch."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from domain.research.search import SearchBatch, SearchResult

_LOW_VALUE_HOSTS = frozenset(
    {
        "emergentmind.com",
        "papertik.app",
        "quora.com",
        "reddit.com",
        "scribd.com",
        "stackexchange.com",
    }
)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "ref", "source"})
_DOI = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.IGNORECASE)
_GENERIC_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "for",
        "of",
        "in",
        "on",
        "with",
        "from",
        "insider",
        "threat",
        "detection",
        "cert",
        "data",
        "dataset",
        "datasets",
        "research",
        "study",
        "paper",
        "official",
        "documentation",
        "review",
        "deep",
        "learning",
        "bridging",
        "gap",
        "doi",
        "method",
        "methods",
        "controlled",
        "retrieval",
        "query",
    }
)
_WORDS = re.compile(r"[a-z][a-z0-9.-]{2,}", re.IGNORECASE)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _low_value(candidate: SearchResult) -> bool:
    host = _host(candidate.url)
    if any(host == item or host.endswith("." + item) for item in _LOW_VALUE_HOSTS):
        return True
    path = urlsplit(candidate.url).path.casefold()
    return (
        host == "scirp.org"
        and path.startswith("/reference/referencespapers")
        or host == "bibbase.org"
        and path.startswith("/network/publication")
        or host == "semanticscholar.org"
        and path.startswith("/paper/")
        or host == "researchr.org"
        and "/references" in path
    )


def normalized_url(url: str) -> str:
    parts = urlsplit(url)
    host = _host(url)
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
        )
    )
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def candidate_identity(candidate: SearchResult) -> str:
    """DOI takes precedence over mirrors; otherwise normalize the fetch target."""
    for value in (candidate.url, candidate.fulltext_url or ""):
        match = _DOI.search(value)
        if match:
            return "doi:" + match.group().rstrip("/.,;)").lower()
    for value in (candidate.fulltext_url or "", candidate.url):
        parts = urlsplit(value)
        if _host(value) == "arxiv.org":
            match = re.fullmatch(r"/(?:abs|pdf)/([^/]+)", parts.path, re.IGNORECASE)
            if match:
                identifier = re.sub(
                    r"(?:v\d+)?(?:\.pdf)?$", "", match.group(1), flags=re.IGNORECASE
                )
                return "arxiv:" + identifier.casefold()
    return normalized_url(candidate.fulltext_url or candidate.url)


def _score(candidate: SearchResult, query: str) -> int:
    host = _host(candidate.url)
    title = candidate.title.casefold()
    terms = set(re.findall(r"[a-z][a-z0-9-]{3,}", query.casefold()))
    score = 2 * len(terms & set(re.findall(r"[a-z][a-z0-9-]{3,}", title)))
    if host in {"kilthub.cmu.edu", "figshare.com", "sei.cmu.edu"}:
        score += 12
    elif host.endswith((".edu", ".gov")):
        score += 4
    if candidate.source_type == "paper":
        score += 3
    if candidate.fulltext_url:
        score += 2
    if any(term in title for term in ("configuration manual", "user manual")):
        score -= 12
    if any(term in title for term in ("master's thesis", "dissertation")):
        score -= 4
    return score


def title_identity(title: str) -> str:
    return " ".join(re.findall(r"\w+", title.casefold()))


def _relevant(candidate: SearchResult, query: str) -> bool:
    site = re.search(r"\bsite:([a-z0-9.-]+)", query, re.IGNORECASE)
    if site:
        domain = site.group(1).lower().removeprefix("www.")
        host = _host(candidate.url)
        if host != domain and not host.endswith("." + domain):
            return False
    title = title_identity(candidate.title)
    phrases = re.findall(r'"([^"]{4,})"', query)
    target = urlsplit(candidate.fulltext_url or candidate.url)
    is_original_pdf = target.path.casefold().endswith(".pdf") or (
        (target.hostname or "").casefold() in {"arxiv.org", "www.arxiv.org"}
        and target.path.startswith("/pdf/")
    )
    excerpt = title_identity(candidate.snippet[:1000]) if is_original_pdf else ""
    for phrase in phrases:
        wanted = title_identity(phrase)
        if wanted in title:
            continue
        if wanted not in excerpt:
            return False
        # A PDF excerpt can quote another paper in its references. Require
        # its displayed title to identify most of the requested work too.
        wanted_words = set(wanted.split())
        if len(wanted_words & set(title.split())) < max(2, (len(wanted_words) + 1) // 2):
            return False
    if phrases:
        return True
    terms = {
        term.casefold()
        for term in _WORDS.findall(query)
        if term.casefold() not in _GENERIC_TERMS and not term.isdigit()
    }
    if not terms:
        return True
    text = " ".join(
        [candidate.title, candidate.snippet[:2000], candidate.url, *candidate.authors_or_publisher]
    ).casefold()
    matched = {term for term in terms if term in text}
    return len(matched) >= min(2, len(terms))


def select_candidates(
    batch: SearchBatch,
    query: str,
    *,
    seen: set[str] | None = None,
    seen_titles: set[str] | None = None,
    per_provider: int = 4,
    total_limit: int = 6,
) -> tuple[list[SearchResult], int]:
    """Prefer relevant originals and keep each provider from crowding out the rest."""
    seen = seen or set()
    seen_titles = seen_titles or set()
    ranked = []
    rejected = 0
    for outcome in batch.outcomes:
        for rank, candidate in enumerate(outcome.items):
            if _low_value(candidate) or not _relevant(candidate, query):
                rejected += 1
                continue
            identity = candidate_identity(candidate)
            title = title_identity(candidate.title)
            if (
                identity in seen
                or normalized_url(candidate.fulltext_url or candidate.url) in seen
                or (len(title) >= 40 and title in seen_titles)
            ):
                rejected += 1
                continue
            ranked.append(
                (-_score(candidate, query), rank, outcome.source, identity, title, candidate)
            )
    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    selected, identities, titles, provider_counts = [], set(), set(), {}
    for _, _, provider, identity, title, candidate in ranked:
        if identity in identities or (len(title) >= 40 and title in titles):
            rejected += 1
            continue
        if provider_counts.get(provider, 0) >= per_provider:
            continue
        selected.append(candidate)
        identities.add(identity)
        titles.add(title)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        if len(selected) >= total_limit:
            break
    return selected, rejected
