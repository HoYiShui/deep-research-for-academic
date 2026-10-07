"""Retired legacy dependency probe; does not load .env or contact adapters."""

import sys


def main() -> int:
    print(
        "smoke_real is retired: it used a hard-coded legacy database, wrote probe "
        "records and made paid model calls without explicit selection.\n"
        "No network requests or database writes were performed.\n"
        "From backend, use:\n"
        "  uv run python -m cli doctor --json  (no paid model calls)\n"
        "  uv run python -m scripts.verify_clarify_http --help\n"
        "  uv run python -m scripts.verify_run_http --help\n"
        "For paid single-phase validation, see cli/README.md. "
        "A passing dependency probe is not a successful research run.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
