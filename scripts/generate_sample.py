"""Regenerate data/sample_messy.csv — a deterministic, deliberately messy dataset.

Run from the project root:  py scripts/generate_sample.py

The output (~200 rows) exercises every cleaning feature:
- exact duplicate rows
- leading/trailing whitespace in names and countries
- messy column names (spaces, camelCase, symbols)
- mixed types: "$" prefixes, "N/A"/"unknown" strings inside numeric columns
- mixed date formats plus a few unparseable date strings
- scattered nulls
- numeric outliers (huge spends, impossible ages)
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_messy.csv"

HEADER = [
    "Order ID",
    "Customer Name",
    "EMAIL Address",
    "signup-date",
    "Plan Type",
    "Monthly Spend ($)",
    "AGE",
    "Country/Region",
    "satisfactionScore",
]

FIRST = ["Alice", "Bob", "Carmen", "Dmitri", "Elena", "Farid", "Grace", "Hiro",
         "Ines", "Jamal", "Kira", "Liam", "Mona", "Noah", "Olga", "Pedro",
         "Quinn", "Rosa", "Sven", "Tara"]
LAST = ["Anderson", "Baker", "Chen", "Diaz", "Evans", "Fischer", "Garcia",
        "Huang", "Ivanov", "Jensen", "Khan", "Lopez", "Muller", "Nakamura",
        "Okafor", "Patel"]
COUNTRIES = ["USA", "Canada", "Germany", "Japan", "Brazil", "India", "France", "Nigeria"]
PLANS = ["Basic", "Pro", "pro", "PRO", "Enterprise", "basic "]


def _maybe_pad(value: str, rng: random.Random, chance: float = 0.25) -> str:
    """Randomly add leading/trailing whitespace."""
    if rng.random() < chance:
        return " " * rng.randint(1, 3) + value + " " * rng.randint(0, 2)
    return value


def _date(rng: random.Random) -> str:
    year = rng.randint(2021, 2024)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    style = rng.random()
    if style < 0.5:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if style < 0.8:
        return f"{month:02d}/{day:02d}/{year:04d}"
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[month - 1]} {day}, {year}"


def build_rows(seed: int = 42) -> list[list[str]]:
    rng = random.Random(seed)
    rows: list[list[str]] = []

    for i in range(180):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.split()[0].lower()}.{name.split()[1].lower()}{rng.randint(1, 99)}@example.com"

        # signup date: mostly valid mixed formats, some junk, some blank
        roll = rng.random()
        if roll < 0.04:
            date = "pending"
        elif roll < 0.08:
            date = ""
        else:
            date = _date(rng)

        # monthly spend: numeric text, "$" prefixed, N/A, blanks, outliers
        roll = rng.random()
        if roll < 0.06:
            spend = "N/A"
        elif roll < 0.10:
            spend = ""
        elif roll < 0.16:
            spend = f"${rng.uniform(10, 250):.2f}"
        elif roll < 0.19:
            spend = f"{rng.uniform(4000, 9999):.2f}"  # outlier
        else:
            spend = f"{rng.uniform(10, 250):.2f}"

        # age: mostly sane, a few impossible, some blank / text
        roll = rng.random()
        if roll < 0.03:
            age = str(rng.choice([-3, 187, 220, 999]))  # outlier
        elif roll < 0.08:
            age = ""
        elif roll < 0.10:
            age = "unknown"
        else:
            age = str(rng.randint(18, 75))

        satisfaction = "" if rng.random() < 0.07 else str(rng.randint(1, 10))
        email_out = "" if rng.random() < 0.05 else email

        rows.append([
            f"ORD-{1000 + i}",
            _maybe_pad(name, rng),
            email_out,
            date,
            rng.choice(PLANS),
            spend,
            age,
            _maybe_pad(rng.choice(COUNTRIES), rng, chance=0.2),
            satisfaction,
        ])

    # Exact duplicates: copy 20 existing rows verbatim, then shuffle everything.
    duplicates = [list(rng.choice(rows)) for _ in range(20)]
    rows.extend(duplicates)
    rng.shuffle(rows)
    return rows


def main() -> None:
    rows = build_rows()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
