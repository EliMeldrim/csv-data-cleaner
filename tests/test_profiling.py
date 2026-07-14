"""Unit tests for the profiling module."""

from __future__ import annotations

import pandas as pd

from app import profiling


def test_profile_dataframe_shape_and_duplicates() -> None:
    df = pd.DataFrame({"a": [1, 2, 2], "b": ["x", "y", "y"]})
    profile = profiling.profile_dataframe(df)
    assert profile["rows"] == 3
    assert profile["cols"] == 2
    assert profile["duplicate_rows"] == 1
    assert [c["name"] for c in profile["columns"]] == ["a", "b"]


def test_numeric_column_stats_and_outliers() -> None:
    df = pd.DataFrame({"x": [10.0, 11.0, 12.0, 11.0, 10.5, 900.0, None]})
    col = profiling.profile_dataframe(df)["columns"][0]
    assert col["nulls"] == 1
    assert col["min"] == 10.0
    assert col["max"] == 900.0
    assert col["outliers"] == 1
    assert col["inferred_type"] == "float"


def test_text_column_has_no_numeric_stats() -> None:
    df = pd.DataFrame({"name": ["a", "b", None]})
    col = profiling.profile_dataframe(df)["columns"][0]
    assert col["min"] is None and col["max"] is None and col["mean"] is None
    assert col["outliers"] is None
    assert col["null_pct"] == 33.3


def test_numbers_stored_as_text_detected() -> None:
    df = pd.DataFrame({"spend": ["10.5", "20", "30", None]})
    col = profiling.profile_dataframe(df)["columns"][0]
    assert col["inferred_type"] == "numeric (stored as text)"


def test_iqr_fences_none_for_all_null() -> None:
    assert profiling.iqr_fences(pd.Series([None, None], dtype="float64")) is None


def test_iqr_fences_values() -> None:
    fences = profiling.iqr_fences(pd.Series([1.0, 2.0, 3.0, 4.0]))
    assert fences is not None
    low, high = fences
    assert low < 1.0 < 4.0 < high
