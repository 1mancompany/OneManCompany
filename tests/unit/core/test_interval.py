"""Tests for the shared interval parser."""
import pytest
from onemancompany.core.interval import parse_interval


@pytest.mark.parametrize("input_str,expected", [
    ("30s", 30),
    ("5m", 300),
    ("1h", 3600),
    ("2d", 172800),
    ("  10M  ", 600),
])
def test_parse_valid(input_str, expected):
    assert parse_interval(input_str) == expected


@pytest.mark.parametrize("input_str", [
    "", "abc", "5x", "m5", "0", None,
])
def test_parse_invalid(input_str):
    assert parse_interval(input_str) is None
