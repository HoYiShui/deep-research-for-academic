"""ingest command: ingest a PDF into the KB (real-mode)."""

from __future__ import annotations

import uuid

from cli import container, output


async def run(args) -> int:
    c = container.build_container(fake=False, seed=None)
    document_id = uuid.uuid4().hex
    result = await c.knowledge_base.ingest(document_id, args.pdf, args.kb)
    ok = result["status"] == "done"
    status = "ok" if ok else "failed"
    if args.json:
        output.emit_json(
            status,
            {"document_id": result.get("document_id", ""), "chunks": result.get("chunks", 0)},
        )
    else:
        output.emit_human(status, str(result))
    return output.EXIT_SUCCESS if ok else output.EXIT_FAILURE
