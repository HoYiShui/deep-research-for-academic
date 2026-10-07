"""Audit a real stored PDF with local MinerU; not a new download/Run acceptance."""

import argparse
import asyncio
import json
from collections import Counter

from application.settings import Settings
from domain.documents import ParserConfig
from domain.ports import AdapterError
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION
from infrastructure.parser.pdf import MinerUDocumentParser
from infrastructure.storage.content import MinioContentStore
from scripts.verify_cli_plan import save_record


async def verify(args):
    settings = Settings.load()
    store = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
        secure=settings.minio_secure,
    )
    parser = MinerUDocumentParser(store, args.models)
    record = {
        "scope": "cached_original_parser_only",
        "dependency_mode": "real",
        "parser_version": MINERU_PARSER_VERSION,
        "new_download": False,
    }
    try:
        reference = await store.head(args.content_key)
        record["original"] = reference.model_dump(mode="json")
        print("Real local PDF parsing started", flush=True) if not args.json else None
        parsed = await parser.parse(reference, ParserConfig(parser_version=MINERU_PARSER_VERSION))
        record.update(
            {
                "status": "ok",
                "document": parsed.model_dump(mode="json"),
                "block_types": dict(Counter(b.type for b in parsed.blocks)),
                "pages": sorted({b.location.page_start for b in parsed.blocks}),
            }
        )
    except AdapterError as exc:
        record.update({"status": "error", "code": exc.code, "message": exc.message})
    finally:
        await parser.close()
        await store.close()
    if args.record:
        save_record(args.record, record)
    print(json.dumps(record, ensure_ascii=False))
    return 0 if record["status"] == "ok" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-key", required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--record", help="New exclusive evidence file; do not expose private PDFs")
    parser.add_argument("--json", action="store_true")
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
