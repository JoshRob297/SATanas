import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from satanas.sat.sync import months_in_range, rolling_range


def test_months_in_range_same_year():
    r = months_in_range(date(2025, 1, 1), date(2025, 3, 15))
    assert r == [(2025, 1), (2025, 2), (2025, 3)]


def test_months_in_range_cross_year():
    r = months_in_range(date(2025, 11, 1), date(2026, 2, 1))
    assert r == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_rolling_range_12_months():
    start, end = rolling_range(date(2026, 8, 13))
    assert (start.year, start.month) == (2025, 8)
    assert (end.year, end.month) == (2026, 8)
    assert len(months_in_range(start, end)) == 13


def test_rolling_range_january():
    start, end = rolling_range(date(2026, 1, 5))
    assert (start.year, start.month) == (2025, 1)
    assert len(months_in_range(start, end)) == 13
