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


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's text can echo arbitrary queries, filenames or invalid
        # values. Use a safe notice; handler-level field validation is separate.
        raise output.UsageError("Invalid CLI arguments; use --help for command syntax")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    parser.add_argument(
        "--verbose", action="store_true", help="operation metadata to stderr (no prompt/body)"
    )
    parser.add_argument("--quiet", action="store_true", help="only the final report")


def _add_trace(parser):
    parser.add_argument(
        "--trace", help="exclusive local JSONL tool trace file (may contain research queries)"
    )
    parser.add_argument(
        "--trace-content",
        action="store_true",
        help="include model prompts/responses; requires --trace",
    )


async def execute(args):
    if getattr(args, "trace_content", False) and not getattr(args, "trace", None):
        raise output.UsageError("--trace-content requires --trace")
    if not getattr(args, "trace", None):
        return await args.handler(args)
    if args.command == "run" and args.fake:
        raise output.UsageError("Legacy fake run does not support --trace; use phase or run --real")
    from pydantic import SecretStr

    from application.settings import Settings
    from cli.trace import TraceRecorder
    from domain.research.diagnostics import diagnostic_scope

    settings = Settings.load()
    secrets = [
        value.get_secret_value()
        for name in type(settings).model_fields
        if isinstance(value := getattr(settings, name), SecretStr)
    ]
    recorder = TraceRecorder(args.trace, content=args.trace_content, secrets=secrets)
    try:
        with diagnostic_scope(sink=recorder):
            recorder(
                {
                    "event": "trace_started",
                    "command": args.command,
                    "content_enabled": args.trace_content,
                }
            )
            code = await args.handler(args)
            recorder({"event": "trace_finished", "exit_code": code})
            return code
    except BaseException as exc:
        recorder({"event": "trace_failed", "code": getattr(exc, "code", "execution_failed")})
        raise
    finally:
        recorder.close()


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(prog="cli", description="deep-research-agent debug CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="environment check")
    p.add_argument(
        "--scope",
        choices=["all", "research"],
        default="all",
        help="research skips deferred knowledge-base dependency probes",
    )
    p.add_argument("--debug-db", action="store_true", help="check the separate dr4a_debug database")
    _add_common(p)
    p.set_defaults(handler=doctor.run)

    p = sub.add_parser("run", help="run the pipeline from a frozen ResearchBrief")
    p.add_argument("--brief", required=True, help="path to a frozen ResearchBrief JSON")
    p.add_argument(
        "--debug-db", action="store_true", help="use the existing dr4a_debug database (real only)"
    )
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
    _add_trace(p)
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
    _add_trace(p)
    _add_common(p)
    p.set_defaults(handler=phase.run)

    p = sub.add_parser("dump", help="read a session's latest snapshot state")
    p.add_argument("session_id")
    p.add_argument("--debug-db", action="store_true", help="read the separate dr4a_debug database")
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
    values = list(sys.argv[1:] if argv is None else argv)
    flags = values[: values.index("--")] if "--" in values else values
    # Parsing may fail before a Namespace exists. Still honor a requested JSON
    # flag, but not a positional value following the end-of-options delimiter.
    args = argparse.Namespace(json="--json" in flags)
    try:
        args = parser.parse_args(values)
        return asyncio.run(execute(args))
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
    except KeyboardInterrupt:
        return output.emit_error(
            args, output.EXIT_FAILURE, "interrupted", "CLI command interrupted"
        )
    except Exception:  # noqa: BLE001 -- provider/SDK exception text may contain credentials
        return output.emit_error(args, output.EXIT_FAILURE, "execution_failed", "Command failed")


if __name__ == "__main__":
    sys.exit(main())
