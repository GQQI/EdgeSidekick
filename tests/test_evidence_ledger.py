"""The goal judge must still see tool results that context management cleared."""

from __future__ import annotations

from metateam.runtime.evidence import EvidenceLedger


def test_rows_are_scoped_by_mark() -> None:
    ledger = EvidenceLedger()
    ledger.record("read_file", "old")
    mark = ledger.mark()
    ledger.record("write_file", "new")
    assert ledger.rows() == [("read_file", "old"), ("write_file", "new")]
    assert ledger.rows(start=mark) == [("write_file", "new")]


def test_count_is_monotonic_after_trimming() -> None:
    ledger = EvidenceLedger(max_entries=2)
    for i in range(5):
        ledger.record("read_file", f"line {i}")
    assert ledger.count() == 5
    assert ledger.rows() == [("read_file", "line 3"), ("read_file", "line 4")]


def test_empty_results_are_not_recorded() -> None:
    ledger = EvidenceLedger()
    ledger.record("read_file", "   ")
    assert ledger.count() == 0
    assert ledger.rows() == []


def test_reset_clears_both_entries_and_sequence() -> None:
    ledger = EvidenceLedger()
    ledger.record("read_file", "x")
    ledger.reset()
    assert ledger.count() == 0
    assert ledger.rows() == []
    ledger.record("read_file", "y")
    assert ledger.rows() == [("read_file", "y")]
