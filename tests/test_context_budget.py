from metateam.core.config import working_context_budget


def test_working_budget_is_a_fraction_of_a_large_window() -> None:
    assert working_context_budget(204_800) == int(204_800 * 0.45)
    assert working_context_budget(1_000_000) == 96_000


def test_working_budget_stays_inside_a_small_window() -> None:
    assert working_context_budget(32_768) == 32_768
    assert working_context_budget(64_000) == 56_000
    assert working_context_budget(0) == 48_000


def test_explicit_budget_is_kept_unless_it_is_the_whole_window() -> None:
    assert working_context_budget(204_800, 96_000) == 96_000
    assert working_context_budget(204_800, 204_800) == int(204_800 * 0.45)
