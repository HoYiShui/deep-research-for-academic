"""``python -m cli`` entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys

from cli import output
from cli.commands import doctor, dump, ingest, run, search, slice


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    parser.add_argument("--verbose", action="store_true", help="LLM prompts/responses to stderr")
    parser.add_argument("--quiet", action="store_true", help="only the final report")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="deep-research-agent debug CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="environment check")
    _add_common(p)
    p.set_defaults(handler=doctor.run)

    p = sub.add_parser("run", help="full pipeline (clarify -> pipeline -> report)")
    p.add_argument("query", nargs="?", default=None)
    p.add_argument("--brief-file", help="skip clarify; load a frozen brief JSON")
    p.add_argument("--answers", help="canned clarify answers (one per line)")
    p.add_argument("--fake", action=argparse.BooleanOptionalAction, default=True,
                   help="use fake adapters (default); --no-fake for real")
    p.add_argument("--seed", type=int, help="fake-mode seed")
    p.add_argument("--max-iterations", type=int, help="rework cap")
    _add_common(p)
    p.set_defaults(handler=run.run)

    p = sub.add_parser("slice", help="run a single phase on a canned state")
    p.add_argument("phase", choices=["plan", "research", "analyze", "write", "review"])
    p.add_argument("--input", required=True, help="canned PipelineState JSON")
    p.add_argument("--fake", action=argparse.BooleanOptionalAction, default=True,
                   help="use fake adapters (default); --no-fake for real")
    p.add_argument("--seed", type=int, help="fake-mode seed")
    _add_common(p)
    p.set_defaults(handler=slice.run)

    p = sub.add_parser("dump", help="read a session's latest snapshot state")
    p.add_argument("session_id")
    _add_common(p)
    p.set_defaults(handler=dump.run)

    p = sub.add_parser("ingest", help="ingest a PDF into the KB")
    p.add_argument("pdf")
    p.add_argument("--kb", default="default")
    _add_common(p)
    p.set_defaults(handler=ingest.run)

    p = sub.add_parser("search", help="search the KB")
    p.add_argument("query")
    p.add_argument("--kb", default="default")
    _add_common(p)
    p.set_defaults(handler=search.run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.handler(args))
    except output.UsageError as exc:
        output.log(f"usage error: {exc}")
        return output.EXIT_USAGE
    except output.EnvError as exc:
        output.log(f"environment error: {exc}")
        return output.EXIT_ENV
    except Exception as exc:  # noqa: BLE001 — surface any unexpected error to stderr
        output.log(f"error: {type(exc).__name__}: {exc}")
        return output.EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
