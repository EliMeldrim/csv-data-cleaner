"""Unit tests for the pure cleaning operations."""

from __future__ import annotations

import pandas as pd
import pytest

from app import cleaning
from app.cleaning import CleaningError


@pytest.fixture()
def messy_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Order ID": ["A1", "A2", "A2", "A3"],
            " Customer Name ": ["  Alice ", "Bob", "Bob", " Carol"],
            "monthlySpend": ["10.5", "20", "20", "not a number"],
            "AGE": [25.0, 30.0, 30.0, None],
        }
    )


# ------------------------------------------------------------- duplicates

def test_drop_duplicates_removes_exact_dups(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.drop_duplicates(messy_df)
    assert len(out) == 3
    assert "1 duplicate row" in summary
    # input untouched
    assert len(messy_df) == 4


def test_drop_duplicates_noop() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    out, summary = cleaning.drop_duplicates(df)
    assert len(out) == 3
    assert "0 duplicate rows" in summary


# ------------------------------------------------------------- whitespace

def test_trim_whitespace(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.trim_whitespace(messy_df)
    assert out[" Customer Name "].tolist() == ["Alice", "Bob", "Bob", "Carol"]
    assert "2 values" in summary
    # numeric column untouched, nulls preserved
    assert out["AGE"].isna().sum() == 1


def test_trim_whitespace_only_counts_changes() -> None:
    df = pd.DataFrame({"a": ["clean", "also clean"]})
    _, summary = cleaning.trim_whitespace(df)
    assert "0 values" in summary


# ----------------------------------------------------------- column names

def test_normalize_column_names(messy_df: pd.DataFrame) -> None:
    out, _ = cleaning.normalize_column_names(messy_df)
    assert list(out.columns) == ["order_id", "customer_name", "monthly_spend", "age"]


def test_normalize_column_names_deduplicates_collisions() -> None:
    df = pd.DataFrame([[1, 2, 3]], columns=["My Col", "my-col", "my_col"])
    out, _ = cleaning.normalize_column_names(df)
    assert list(out.columns) == ["my_col", "my_col_2", "my_col_3"]


def test_snake_case_handles_symbols_and_camel() -> None:
    assert cleaning._snake_case("Monthly Spend ($)") == "monthly_spend"
    assert cleaning._snake_case("satisfactionScore") == "satisfaction_score"
    assert cleaning._snake_case("Country/Region") == "country_region"
    assert cleaning._snake_case("***") == "column"


# ---------------------------------------------------------------- coercion

def test_coerce_numeric_reports_failures(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.coerce_type(messy_df, column="monthlySpend", target="numeric")
    assert out["monthlySpend"].dtype.kind == "f"
    assert out["monthlySpend"].isna().sum() == 1  # "not a number"
    assert "1 value failed coercion" in summary


def test_coerce_numeric_strips_whitespace_first() -> None:
    df = pd.DataFrame({"x": [" 42 ", "7"]})
    out, summary = cleaning.coerce_type(df, column="x", target="numeric")
    assert out["x"].tolist() == [42.0, 7.0]
    assert "failed" not in summary


def test_coerce_datetime_mixed_formats() -> None:
    df = pd.DataFrame({"d": ["2023-01-15", "01/15/2023", "Jan 15, 2023", "garbage", None]})
    out, summary = cleaning.coerce_type(df, column="d", target="datetime")
    parsed = out["d"]
    assert parsed.notna().sum() == 3
    assert all(ts.year == 2023 and ts.month == 1 and ts.day == 15 for ts in parsed.dropna())
    assert "1 value failed coercion" in summary  # None was already null, not a failure


def test_coerce_string_never_fails(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.coerce_type(messy_df, column="AGE", target="string")
    assert str(out["AGE"].dtype) == "string"
    assert "failed" not in summary


def test_coerce_rejects_bad_inputs(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.coerce_type(messy_df, column="nope", target="numeric")
    with pytest.raises(CleaningError):
        cleaning.coerce_type(messy_df, column="AGE", target="boolean")


# ------------------------------------------------------------------ nulls

def test_nulls_drop_rows(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.handle_nulls(messy_df, column="AGE", strategy="drop")
    assert len(out) == 3
    assert out["AGE"].isna().sum() == 0
    assert "Dropped 1 row" in summary


def test_nulls_fill_value_numeric_column(messy_df: pd.DataFrame) -> None:
    out, _ = cleaning.handle_nulls(messy_df, column="AGE", strategy="value", fill_value="99")
    assert out["AGE"].tolist() == [25.0, 30.0, 30.0, 99.0]


def test_nulls_fill_value_rejects_non_number_for_numeric(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.handle_nulls(messy_df, column="AGE", strategy="value", fill_value="oops")


def test_nulls_fill_value_requires_value(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.handle_nulls(messy_df, column="AGE", strategy="value")


def test_nulls_fill_mean_median_mode() -> None:
    df = pd.DataFrame({"x": [1.0, 2.0, 2.0, None], "label": ["a", "a", None, "b"]})
    out, _ = cleaning.handle_nulls(df, column="x", strategy="mean")
    assert out["x"].iloc[3] == pytest.approx(5 / 3)
    out, _ = cleaning.handle_nulls(df, column="x", strategy="median")
    assert out["x"].iloc[3] == 2.0
    out, _ = cleaning.handle_nulls(df, column="label", strategy="mode")
    assert out["label"].iloc[2] == "a"


def test_nulls_mean_rejects_text_column(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.handle_nulls(messy_df, column="monthlySpend", strategy="mean")


def test_nulls_noop_when_no_nulls(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.handle_nulls(messy_df, column="Order ID", strategy="drop")
    assert len(out) == len(messy_df)
    assert "nothing to change" in summary


# --------------------------------------------------------------- outliers

@pytest.fixture()
def outlier_df() -> pd.DataFrame:
    return pd.DataFrame({"x": [10.0, 11.0, 12.0, 11.5, 10.5, 500.0, None]})


def test_outliers_flag(outlier_df: pd.DataFrame) -> None:
    out, summary = cleaning.handle_outliers(outlier_df, column="x", method="flag")
    assert "x_is_outlier" in out.columns
    assert int(out["x_is_outlier"].sum()) == 1
    assert bool(out.loc[5, "x_is_outlier"]) is True
    assert bool(out.loc[6, "x_is_outlier"]) is False  # null is not an outlier
    assert "Flagged 1 outlier" in summary


def test_outliers_clip(outlier_df: pd.DataFrame) -> None:
    out, summary = cleaning.handle_outliers(outlier_df, column="x", method="clip")
    assert out["x"].max() < 500.0
    assert out["x"].isna().sum() == 1  # nulls survive clipping
    assert "Clipped 1 outlier" in summary


def test_outliers_flag_column_collision(outlier_df: pd.DataFrame) -> None:
    once, _ = cleaning.handle_outliers(outlier_df, column="x", method="flag")
    twice, _ = cleaning.handle_outliers(once, column="x", method="flag")
    assert "x_is_outlier_2" in twice.columns


def test_outliers_rejects_text_column(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.handle_outliers(messy_df, column="monthlySpend", method="flag")


def test_outliers_rejects_bad_factor(outlier_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError):
        cleaning.handle_outliers(outlier_df, column="x", method="clip", factor=-1)


# --------------------------------------------------------------- dispatch

def test_apply_operation_dispatch(messy_df: pd.DataFrame) -> None:
    out, summary = cleaning.apply_operation(messy_df, "drop_duplicates", {})
    assert len(out) == 3
    assert summary


def test_apply_operation_unknown_op(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError, match="Unknown operation"):
        cleaning.apply_operation(messy_df, "delete_everything", {})


def test_apply_operation_bad_params(messy_df: pd.DataFrame) -> None:
    with pytest.raises(CleaningError, match="Invalid parameters"):
        cleaning.apply_operation(messy_df, "coerce_type", {"bogus": True})
