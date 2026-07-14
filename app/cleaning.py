"""Cleaning operations on pandas DataFrames.

Every operation is a pure function: it takes a DataFrame (plus parameters)
and returns a ``(cleaned_df, summary)`` tuple, where ``summary`` is a
human-readable description of what changed. Inputs are never mutated.

``apply_operation`` is the single dispatch point used by the API layer, so
the pipeline recorded per session is just ``[{"op": ..., "params": ...}]``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import pandas as pd

from .profiling import iqr_fences

CleanResult = tuple[pd.DataFrame, str]

VALID_COERCE_TARGETS = ("numeric", "datetime", "string")
VALID_NULL_STRATEGIES = ("drop", "value", "mean", "median", "mode")
VALID_OUTLIER_METHODS = ("flag", "clip")


class CleaningError(ValueError):
    """Raised when a cleaning operation receives invalid parameters."""


def _require_column(df: pd.DataFrame, column: str) -> None:
    if column not in df.columns:
        raise CleaningError(f"Column {column!r} does not exist")


def _is_true_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def drop_duplicates(df: pd.DataFrame) -> CleanResult:
    """Drop exact duplicate rows, keeping the first occurrence."""
    before = len(df)
    out = df.drop_duplicates().reset_index(drop=True)
    removed = before - len(out)
    return out, f"Removed {_plural(removed, 'duplicate row')}"


def trim_whitespace(df: pd.DataFrame) -> CleanResult:
    """Strip leading/trailing whitespace from every string cell."""
    out = df.copy()
    changed = 0
    for col in out.columns:
        series = out[col]
        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        stripped = series.map(lambda v: v.strip() if isinstance(v, str) else v)
        changed += int(((stripped != series) & series.notna()).sum())
        out[col] = stripped
    return out, f"Trimmed whitespace from {_plural(changed, 'value')}"


def _snake_case(name: str) -> str:
    name = name.strip()
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)  # camelCase boundaries
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name.lower() or "column"


def normalize_column_names(df: pd.DataFrame) -> CleanResult:
    """Rename all columns to snake_case, de-duplicating collisions."""
    out = df.copy()
    used: set[str] = set()
    new_names: list[str] = []
    renamed = 0
    for col in out.columns:
        base = _snake_case(str(col))
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        new_names.append(name)
        if name != str(col):
            renamed += 1
    out.columns = new_names
    return out, f"Renamed {_plural(renamed, 'column')} to snake_case"


def coerce_type(df: pd.DataFrame, column: str, target: str) -> CleanResult:
    """Coerce a column to numeric, datetime, or string.

    Values that cannot be coerced become null; the count of such failures
    is reported in the summary.
    """
    _require_column(df, column)
    if target not in VALID_COERCE_TARGETS:
        raise CleaningError(
            f"Unknown target type {target!r}; expected one of {VALID_COERCE_TARGETS}"
        )
    out = df.copy()
    series = out[column]

    if target == "numeric":
        prepared = series.map(lambda v: v.strip() if isinstance(v, str) else v)
        coerced = pd.to_numeric(prepared, errors="coerce")
    elif target == "datetime":
        coerced = pd.to_datetime(series, errors="coerce", format="mixed")
    else:  # string — lossless, no failures possible
        coerced = series.astype("string")

    failures = 0 if target == "string" else int((coerced.isna() & series.notna()).sum())
    out[column] = coerced

    summary = f'Coerced "{column}" to {target}'
    if failures:
        summary += f" — {_plural(failures, 'value')} failed coercion and were set to null"
    return out, summary


def handle_nulls(
    df: pd.DataFrame,
    column: str,
    strategy: str,
    fill_value: Any = None,
) -> CleanResult:
    """Handle nulls in one column: drop rows, or fill with value/mean/median/mode."""
    _require_column(df, column)
    if strategy not in VALID_NULL_STRATEGIES:
        raise CleaningError(
            f"Unknown null strategy {strategy!r}; expected one of {VALID_NULL_STRATEGIES}"
        )
    out = df.copy()
    series = out[column]

    # Validate parameters up front so nonsensical steps fail fast even when
    # the column currently has no nulls.
    if strategy in ("mean", "median") and not _is_true_numeric(series):
        raise CleaningError(
            f'Strategy {strategy!r} requires a numeric column — coerce "{column}" first'
        )
    if strategy == "value" and fill_value is None:
        raise CleaningError("fill_value is required for strategy 'value'")

    nulls = int(series.isna().sum())
    if nulls == 0:
        return out, f'No nulls in "{column}" — nothing to change'

    if strategy == "drop":
        out = out[series.notna()].reset_index(drop=True)
        return out, f"Dropped {_plural(nulls, 'row')} with null \"{column}\""

    if strategy == "value":
        value: Any = fill_value
        if _is_true_numeric(series):
            try:
                value = float(fill_value)
            except (TypeError, ValueError) as exc:
                raise CleaningError(
                    f'"{column}" is numeric but fill value {fill_value!r} is not a number'
                ) from exc
        out[column] = series.fillna(value)
        return out, f'Filled {_plural(nulls, "null")} in "{column}" with {value!r}'

    if strategy in ("mean", "median"):
        stat = float(series.mean()) if strategy == "mean" else float(series.median())
        out[column] = series.fillna(stat)
        return out, (
            f'Filled {_plural(nulls, "null")} in "{column}" with {strategy} ({round(stat, 4)})'
        )

    # strategy == "mode"
    modes = series.mode(dropna=True)
    if modes.empty:
        raise CleaningError(f'"{column}" has no mode (all values are null)')
    mode_value = modes.iloc[0]
    out[column] = series.fillna(mode_value)
    return out, f'Filled {_plural(nulls, "null")} in "{column}" with mode ({mode_value!r})'


def handle_outliers(
    df: pd.DataFrame,
    column: str,
    method: str,
    factor: float = 1.5,
) -> CleanResult:
    """Flag or clip IQR outliers in a numeric column.

    ``flag`` adds a boolean ``<column>_is_outlier`` column; ``clip`` caps
    values to the IQR fences.
    """
    _require_column(df, column)
    if method not in VALID_OUTLIER_METHODS:
        raise CleaningError(
            f"Unknown outlier method {method!r}; expected one of {VALID_OUTLIER_METHODS}"
        )
    try:
        factor = float(factor)
    except (TypeError, ValueError) as exc:
        raise CleaningError(f"factor must be a number, got {factor!r}") from exc
    if factor <= 0:
        raise CleaningError("factor must be positive")

    series = df[column]
    if not _is_true_numeric(series):
        raise CleaningError(f'"{column}" is not numeric — coerce it to numeric first')
    fences = iqr_fences(series, factor)
    if fences is None:
        raise CleaningError(f'"{column}" has no numeric values to compute fences from')

    low, high = fences
    mask = ((series < low) | (series > high)).fillna(False).astype(bool)
    count = int(mask.sum())
    out = df.copy()

    if method == "flag":
        flag_col = f"{column}_is_outlier"
        suffix = 2
        while flag_col in out.columns:
            flag_col = f"{column}_is_outlier_{suffix}"
            suffix += 1
        out[flag_col] = mask
        return out, (
            f'Flagged {_plural(count, "outlier")} in "{column}" '
            f'(fence [{low:.4g}, {high:.4g}]) in new column "{flag_col}"'
        )

    out[column] = series.clip(lower=low, upper=high)
    return out, f'Clipped {_plural(count, "outlier")} in "{column}" to [{low:.4g}, {high:.4g}]'


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Callable[..., CleanResult]] = {
    "drop_duplicates": drop_duplicates,
    "trim_whitespace": trim_whitespace,
    "normalize_column_names": normalize_column_names,
    "coerce_type": coerce_type,
    "handle_nulls": handle_nulls,
    "handle_outliers": handle_outliers,
}

OPERATION_NAMES: tuple[str, ...] = tuple(_HANDLERS)


def apply_operation(df: pd.DataFrame, op: str, params: dict[str, Any]) -> CleanResult:
    """Apply a named operation with keyword params; raise CleaningError on bad input."""
    handler = _HANDLERS.get(op)
    if handler is None:
        raise CleaningError(f"Unknown operation {op!r}; expected one of {OPERATION_NAMES}")
    try:
        return handler(df, **params)
    except TypeError as exc:
        raise CleaningError(f"Invalid parameters for {op!r}: {exc}") from exc
