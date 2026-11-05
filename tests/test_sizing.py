from herdr_repetui import sizing
from herdr_repetui.sizing import split_ratio


def kept(r: float) -> float:
    return r if sizing.RATIO_IS_TARGET_SHARE else 1 - r


def test_bottom_quarter_of_full_tab() -> None:
    assert round(74 * (1 - kept(split_ratio(0.25, 74, 74, 6)))) == 18


def test_side_fifth_of_tab_even_when_target_is_half() -> None:
    assert abs(132 * (1 - kept(split_ratio(0.2, 264, 132, 40))) - 53) <= 1


def test_minimum_applies() -> None:
    assert round(120 * (1 - kept(split_ratio(0.2, 120, 120, 40)))) >= 40


def test_clamped() -> None:
    for r in (split_ratio(0.25, 74, 8, 6), split_ratio(0.01, 74, 74, 1), split_ratio(0.2, 10, 0, 40)):
        assert 0.1 <= r <= 0.9


def test_unknown_target_size_splits_in_half() -> None:
    assert split_ratio(0.25, 74, 0, 6) == 0.5
    assert split_ratio(0.25, 74, -3, 6) == 0.5
