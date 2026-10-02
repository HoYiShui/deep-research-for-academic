"""Contract test for CancellationPort (in-memory)."""

from application.ports import CancellationPort
from infrastructure.storage.memory import InMemoryCancel


def test_cancel_defaults_to_not_cancelled() -> None:
    cancel: CancellationPort = InMemoryCancel()
    assert cancel.is_cancelled("s1") is False


def test_cancel_sets_and_reads_flag() -> None:
    cancel: CancellationPort = InMemoryCancel()
    cancel.set_cancelled("s1")
    assert cancel.is_cancelled("s1") is True
    assert cancel.is_cancelled("s2") is False
