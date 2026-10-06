"""``python -m cli`` entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError

from application.errors import AppError
from cli import output
from cli.commands import doctor, dump, ingest, phase, run, search
from domain.ports import AdapterError


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    parser.add_argument(
        "--verbose", action="store_true", help="operation metadata to stderr (no prompt/body)"
    )
    parser.add_argument("--quiet", action="store_true", help="only the final report")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="deep-research-agent debug CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="environment check")
    _add_common(p)
    p.set_defaults(handler=doctor.run)

    p = sub.add_parser("run", help="run the pipeline from a frozen ResearchBrief")
    p.add_argument("--brief", required=True, help="path to a frozen ResearchBrief JSON")
    p.add_argument("--owner", help="existing owner UUID (required in production)")
    p.add_argument("--sources", default="papers,web", help="comma-separated source categories")
    p.add_argument("--kb", action="append", default=[], help="knowledge base UUID (repeatable)")
    p.add_argument(
        "--real",
        action="store_false",
        dest="fake",
        help="use real adapters and persist the debug run",
    )
    p.add_argument("--seed", type=int, help="fake-mode seed")
    _add_common(p)
    p.set_defaults(handler=run.run)

    p = sub.add_parser("phase", help="run one pipeline phase from a saved state")
    p.add_argument("phase", choices=["plan", "research", "analyze", "write", "review"])
    p.add_argument("--state", required=True, help="path to a PipelineState JSON")
    p.add_argument(
        "--real",
        action="store_false",
        dest="fake",
        help="use real adapters for this isolated phase (no Session/Report writes)",
    )
    p.add_argument("--seed", type=int, help="fake-mode seed")
    _add_common(p)
    p.set_defaults(handler=phase.run)

    p = sub.add_parser("dump", help="read a session's latest snapshot state")
    p.add_argument("session_id")
    p.add_argument("--owner", help="existing owner UUID (required in production)")
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
        return output.emit_error(args, output.EXIT_USAGE, "validation_error", str(exc))
    except output.EnvError as exc:
        return output.emit_error(args, output.EXIT_ENV, "service_not_ready", str(exc))
    except ValidationError:
        return output.emit_error(
            args, output.EXIT_USAGE, "validation_error", "Invalid input or configuration"
        )
    except AppError as exc:
        return output.emit_error(args, output.EXIT_FAILURE, exc.code, exc.message, exc.retryable)
    except AdapterError as exc:
        return output.emit_error(args, output.EXIT_ENV, exc.code, exc.message, exc.retryable)
    except Exception:  # noqa: BLE001 -- provider/SDK exception text may contain credentials
        return output.emit_error(args, output.EXIT_FAILURE, "execution_failed", "Command failed")


if __name__ == "__main__":
    sys.exit(main())
