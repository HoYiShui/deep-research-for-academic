"""Read-only raw-download probe. No Parser, MinIO, Evidence, or formal Run.

From backend: uv run python -m scripts.verify_fetch_download --json
The default target is a public paper; no body bytes or credentials are printed.
"""

import argparse
import asyncio
import json
import time

from domain.ports import AdapterError
from infrastructure.fetch.http import RestrictedDownloader


async def verify(args):
    started = time.monotonic()
    output = {"scope": "raw_download_only", "parsed": False, "stored": False}
    try:
        result = await RestrictedDownloader(timeout_s=args.timeout).download(args.url)
        output.update(
            {
                "status": "ok",
                "url": result.final_url,
                "media_type": result.media_type,
                "bytes": len(result.body),
                "sha256": result.sha256,
                "redirects": len(result.redirects),
            }
        )
    except AdapterError as exc:
        output.update({"status": "failed", "code": exc.code, "retryable": exc.retryable})
    output["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(output, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if output["status"] == "ok" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://arxiv.org/pdf/1706.03762v7")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--json", action="store_true")
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
