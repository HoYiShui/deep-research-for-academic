"""Read-only public-search probe, without PG/MinIO or model calls.

Run from backend: uv run python -m scripts.verify_search_adapters --json
One attempt per provider by default; --retry enables at most two. This probe
is intentionally unmetered and is NOT a formal Run or original-text proof.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from application.settings import Settings
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch


async def verify(args):
    settings = Settings.load()
    timeout = args.timeout
    composite = CompositeSearch(
        [
            ("arxiv", ArxivSearch(timeout_s=timeout)),
            ("bocha", BochaSearch(settings.bocha_api_key.get_secret_value(), timeout_s=timeout)),
        ],
        timeout_s=timeout,
    )
    started = time.monotonic()
    try:
        batch = await composite.search_batch(args.query, retry=args.retry)
    finally:
        await composite.aclose()
    output = {
        "scope": "public_search_candidates_only",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "all_failed": batch.all_failed,
        "sources": [
            {
                "source": item.source,
                "status": item.status,
                "attempts": item.attempts,
                "count": len(item.items),
                "failure": item.failure.model_dump() if item.failure else None,
                "first_candidate": {
                    "url": item.items[0].url,
                    "source_tier": item.items[0].source_tier,
                    "version": item.items[0].version,
                }
                if item.items
                else None,
            }
            for item in batch.outcomes
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=None if args.json else 2))
    return 1 if batch.all_failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="Attention Is All You Need transformer")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    return asyncio.run(verify(args))


if __name__ == "__main__":
    raise SystemExit(main())
