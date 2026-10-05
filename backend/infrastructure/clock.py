"""Production clock adapter; database lease time remains PostgreSQL-owned."""

from datetime import UTC, datetime
from time import monotonic


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return monotonic()
