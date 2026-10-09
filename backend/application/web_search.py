"""One search composition for HTTP, CLI run and isolated phase debugging."""

from hashlib import sha256

from application.search_tools import SearchBinding, SearchProvider
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.search.openalex import OpenAlexSearch
from infrastructure.search.search_router import SearchRouterSearch


def web_search_binding(settings, *, timeout_s=None):
    timeout = settings.search_timeout_s if timeout_s is None else timeout_s
    name = settings.web_search_provider
    if name == "search_router":
        adapter = SearchRouterSearch(
            settings.search_router_url, content=settings.search_router_content, timeout_s=timeout
        )
        digest = sha256(
            f"{settings.search_router_url}|{settings.search_router_content}".encode()
        ).hexdigest()
        revision = f"search-router-v1-{digest}"
    else:
        adapter = BochaSearch(settings.bocha_api_key.get_secret_value(), timeout_s=timeout)
        revision = "bocha-v1"
    sources = [(name, adapter)]
    categories = {name: "web"}
    providers = [SearchProvider(name=name, category="web", revision=revision)]
    paper = settings.paper_search_provider
    if paper == "openalex":
        sources.append(("openalex", OpenAlexSearch(timeout_s=timeout)))
        providers.append(
            SearchProvider(name="openalex", category="papers", revision="openalex-works-oa-pdf-v1")
        )
    elif paper == "arxiv":
        sources.append(("arxiv", ArxivSearch(timeout_s=timeout)))
        providers.append(SearchProvider(name="arxiv", category="papers", revision="arxiv-atom-v1"))
    if paper != "none":
        categories[paper] = "papers"
    composite = CompositeSearch(sources, timeout_s=timeout, source_categories=categories)
    return SearchBinding(composite, tuple(providers))
