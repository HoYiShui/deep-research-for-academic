"""Real-service smoke test: hit deepseek / arxiv / bocha / postgres once each.

Run after `docker compose up -d` + schema apply. Loads backend/.env for keys.
Prints PASS/FAIL per dependency and exits non-zero if any probe fails.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_env(path: str) -> None:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


async def _probe_deepseek() -> None:
    from infrastructure.llm.deepseek import DeepSeekLLM

    llm = DeepSeekLLM()
    out = await llm.complete("Reply with exactly one word: pong")
    print(f"      deepseek -> {out.strip()!r}")


async def _probe_arxiv() -> None:
    from infrastructure.search.arxiv import ArxivSearch

    results = await ArxivSearch().search("intrusion detection")
    first = results[0].title if results else "(none)"
    print(f"      arxiv -> {len(results)} results; first: {first!r}")


async def _probe_bocha() -> None:
    from infrastructure.search.bocha import BochaSearch

    results = await BochaSearch().search("large language model")
    first = results[0].title if results else "(none)"
    print(f"      bocha -> {len(results)} results; first: {first!r}")


async def _probe_postgres() -> None:
    from infrastructure.storage.postgres import PostgresStateStore

    store = PostgresStateStore("postgresql://deepresearch:deepresearch@localhost:5433/deepresearch")
    await store.create_session("smoke-1")
    await store.append_message("smoke-1", "user", "probe")
    await store.save_brief("smoke-1", {"query": "probe"}, task_type="idea_exploration")
    await store.save_snapshot("smoke-1", "plan", {"ok": True})
    brief = await store.load_brief("smoke-1")
    snap = await store.load_latest_snapshot("smoke-1", "plan")
    assert brief["query"] == "probe", brief
    assert snap == {"ok": True}, snap
    print(f"      postgres -> 7-table roundtrip OK (brief={brief})")


async def _check(name: str, coro) -> bool:
    try:
        await coro
        print(f"[PASS] {name}")
        return True
    except Exception as exc:  # noqa: BLE001 — smoke test: surface every failure
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return False


async def main() -> None:
    _load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))

    probes = {
        "deepseek (real LLM)": _probe_deepseek(),
        "arxiv (paper search)": _probe_arxiv(),
        "bocha (web search)": _probe_bocha(),
        "postgres (state store)": _probe_postgres(),
    }
    outcomes = {}
    for name, coro in probes.items():
        outcomes[name] = await _check(name, coro)

    print()
    failed = [n for n, ok in outcomes.items() if not ok]
    if failed:
        print(f"{len(failed)} probe(s) failed: {', '.join(failed)}")
        sys.exit(1)
    print("all probes passed")


if __name__ == "__main__":
    asyncio.run(main())
