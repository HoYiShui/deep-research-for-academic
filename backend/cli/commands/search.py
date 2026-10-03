"""search command: search the KB (real-mode)."""

from __future__ import annotations

import json

from cli import container, output


async def run(args) -> int:
    c = container.build_container(fake=False, seed=None)
    chunks = await c.retrieval.retrieve(args.query, args.kb, 20)
    result = {
        "chunks": [{"chunk_id": ch.chunk_id, "text": ch.text, "score": ch.score} for ch in chunks]
    }
    if args.json:
        output.emit_json("ok", result)
    else:
        output.emit_human("ok", json.dumps(result, ensure_ascii=False, indent=2))
    return output.EXIT_SUCCESS
