"""Dataset and per-column profiling for pandas DataFrames.

Pure, read-only functions: nothing here mutates the input frame.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: Mapping from ``pandas.api.types.infer_dtype`` results to friendly labels.
_FRIENDLY_TYPES: dict[str, str] = {
    "integer": "integer",
    "floating": "float",
    "mixed-integer-float": "float",
    "decimal": "float",
    "boolean": "boolean",
    "datetime": "datetime",
    "datetime64": "datetime",
    "date": "datetime",
    "period": "datetime",
    "time": "time",
    "timedelta": "timedelta",
    "timedelta64": "timedelta",
    "string": "text",
    "bytes": "text",
    "categorical": "categorical",
    "mixed-integer": "mixed",
    "mixed": "mixed",
    "empty": "empty",
}


def iqr_fences(series: pd.Series, factor: float = 1.5) -> tuple[float, float] | None:
    """Return the (lower, upper) IQR outlier fences for a numeric series.

    Values below ``Q1 - factor * IQR`` or above ``Q3 + factor * IQR`` are
    considered outliers. Returns ``None`` when the series has no numeric
    values to compute fences from.
    """
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    q1 = float(numeric.quantile(0.25))
    q3 = float(numeric.quantile(0.75))
    iqr = q3 - q1
    return (q1 - factor * iqr, q3 + factor * iqr)


def _to_python_number(value: Any) -> float | int | None:
    """Convert a numpy/pandas scalar into a JSON-safe Python number."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return round(float(value), 4)
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, int):
        return value
    return None


def _is_true_numeric(series: pd.Series) -> bool:
    """Numeric dtype, excluding booleans (which pandas counts as numeric)."""
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def infer_friendly_type(series: pd.Series) -> str:
    """Human-friendly inferred type for a column.

    Object/text columns whose non-null values are mostly parseable numbers
    are reported as ``"numeric (stored as text)"`` — a strong hint that the
    user should run a type coercion step.
    """
    kind = pd.api.types.infer_dtype(series, skipna=True)
    friendly = _FRIENDLY_TYPES.get(kind, kind)
    if friendly in ("text", "mixed"):
        non_null = series.dropna()
        if len(non_null) > 0:
            coerced = pd.to_numeric(non_null, errors="coerce")
            if float(coerced.notna().mean()) >= 0.8:
                return "numeric (stored as text)"
    return friendly


def profile_column(series: pd.Series) -> dict[str, Any]:
    """Profile a single column: type, nulls, uniques, and numeric stats."""
    total = len(series)
    nulls = int(series.isna().sum())
    info: dict[str, Any] = {
        "name": str(series.name),
        "dtype": str(series.dtype),
        "inferred_type": infer_friendly_type(series),
        "nulls": nulls,
        "null_pct": round(100.0 * nulls / total, 1) if total else 0.0,
        "unique": int(series.nunique(dropna=True)),
        "min": None,
        "max": None,
        "mean": None,
        "outliers": None,
    }
    if _is_true_numeric(series):
        non_null = series.dropna()
        if len(non_null) > 0:
            info["min"] = _to_python_number(non_null.min())
            info["max"] = _to_python_number(non_null.max())
            info["mean"] = _to_python_number(non_null.mean())
            fences = iqr_fences(non_null)
            if fences is not None:
                low, high = fences
                info["outliers"] = int(((non_null < low) | (non_null > high)).sum())
    return info


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Profile a whole DataFrame: shape, duplicates, and per-column stats."""
    return {
        "rows": int(len(df)),
        "cols": int(df.shape[1]),
        "duplicate_rows": int(df.duplicated().sum()),
        "columns": [profile_column(df[col]) for col in df.columns],
    }
