"""Retired legacy E2E entry point; intentionally performs no application I/O."""

import sys


def main() -> int:
    print(
        "smoke_e2e is retired: its legacy service calls and implicit Brief approval "
        "do not verify the mono-v1 HTTP contract.\n"
        "No network requests or database writes were performed.\n"
        "From backend, use:\n"
        "  uv run python -m cli doctor --json  (dependency probes, not E2E)\n"
        "  uv run python -m scripts.verify_clarify_http --help\n"
        "  uv run python -m scripts.verify_run_http --help\n"
        "Review the Brief and explicitly approve it before starting a Run. "
        "See cli/README.md and ../tui/README.md for current limits.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
