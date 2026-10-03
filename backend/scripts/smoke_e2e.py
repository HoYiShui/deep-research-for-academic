"""Full real E2E: auth -> clarify -> pipeline -> report over live services.

Runs the real DeepSeek LLM, real arxiv/bocha search, and real PostgreSQL
(localhost:5433). Auth is in-memory; Milvus/sandbox are not exercised (the
minimal pipeline does not touch them).
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


_QUERY = (
    "For a research proposal, compare the detection accuracy of transformer-based "
    "versus CNN-based network intrusion detection systems on the CICIDS2017 and "
    "NSL-KDD datasets, and recommend which approach to pursue."
)


async def main() -> None:
    _load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from application.auth_service import AuthService
    from application.bootstrap import Container
    from infrastructure.fake import FakeExecution, FakeRetrieval
    from infrastructure.search.arxiv import ArxivSearch
    from infrastructure.search.bocha import BochaSearch
    from infrastructure.search.composite import CompositeSearch
    from infrastructure.storage.memory import InMemoryUserStore
    from infrastructure.storage.postgres import PostgresStateStore

    container = Container(
        search=CompositeSearch([("arxiv", ArxivSearch()), ("bocha", BochaSearch())]),
        retrieval=FakeRetrieval(),
        execution=FakeExecution(),
        store=PostgresStateStore("postgresql://deepresearch:deepresearch@localhost:5433/deepresearch"),
        users=InMemoryUserStore(),
        auth=AuthService(InMemoryUserStore(), secret="a" * 32),
    )

    # 1. auth
    await container.auth.register("researcher@lab.org", "password123")
    token = await container.auth.login("researcher@lab.org", "password123")
    print(f"[1] auth: token issued ({len(token['access_token'])} chars)")

    # 2. create session (seeds the query into the brief)
    start = await container.research.start(_QUERY)
    session_id = start["session_id"]
    print(f"[2] session created: {session_id}")

    # 3. clarify (up to 3 rounds)
    answer = _QUERY
    result = {"status": "ask"}
    for _ in range(3):
        result = await container.sessions.clarify_round(session_id, answer)
        print(f"[3] clarify status={result['status']} questions={result['questions']}")
        if result["status"] == "ready":
            break
        answer = "Use reasonable defaults for any unspecified fields and proceed."

    if result["status"] != "ready":
        print("clarify did not reach ready; stopping before pipeline")
        return

    # 4. pipeline (real plan + real search + real persistence)
    print("[4] running pipeline (real LLM plan + real arxiv/bocha search)...")
    task = container.research.spawn_pipeline(session_id, result["brief"])
    await asyncio.wait_for(task, timeout=600)

    # 5. report (non-placeholder prose) from the reports table
    report = await container.research.get_report(session_id)
    status = await container.research.get_status(session_id)
    sections = report.get("sections", {}) if report else {}
    print(f"[5] status={status['status']} report sections={len(sections)}")
    for section_id, s in list(sections.items())[:3]:
        content = s.get("content", "")
        is_placeholder = content.startswith("Section ")
        print(f"      - [{section_id}] placeholder={is_placeholder} content={content[:80]!r}")

    # 6. traceability (SC-002): recovered 'review' snapshot, bindings cite evidence.
    snap = await container.store.load_latest_snapshot(session_id, "review")
    evidence = snap.get("evidence", {})
    claims = snap.get("claims", {})
    bindings = snap.get("draft_claim_bindings", [])
    cited = sum(1 for b in bindings for _ in b.get("cited_evidence_ids", []))
    resolved = sum(
        1 for b in bindings for e in b.get("cited_evidence_ids", []) if e in evidence
    )
    print(
        f"[6] evidence={len(evidence)} claims={len(claims)} bindings={len(bindings)} "
        f"cited={cited} resolved={resolved}"
    )


if __name__ == "__main__":
    asyncio.run(main())
