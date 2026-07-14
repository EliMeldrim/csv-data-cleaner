"""API tests via FastAPI TestClient: happy path plus key error cases."""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.main import SESSIONS, app

client = TestClient(app)

MESSY_CSV = (
    "Order ID, Customer Name ,Monthly Spend ($),AGE\n"
    "A1,  Alice ,10.50,25\n"
    "A2,Bob,$20.00,30\n"
    "A2,Bob,$20.00,30\n"
    "A3, Carol,N/A,220\n"
    "A4,Dave,15.25,\n"
)


@pytest.fixture(autouse=True)
def clean_sessions():
    SESSIONS.clear()
    yield
    SESSIONS.clear()


def _upload(content: str = MESSY_CSV, filename: str = "messy.csv") -> dict:
    response = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _apply(session_id: str, op: str, params: dict | None = None) -> dict:
    response = client.post(
        f"/api/sessions/{session_id}/steps",
        json={"op": op, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_index_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "CSV Data Cleaning Pipeline" in response.text


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_full_happy_path() -> None:
    # 1. upload
    data = _upload()
    session_id = data["session_id"]
    assert data["profile"]["rows"] == 5
    assert data["profile"]["cols"] == 4
    assert data["profile"]["duplicate_rows"] == 1

    # 2. build a pipeline step by step
    data = _apply(session_id, "drop_duplicates")
    assert data["profile"]["rows"] == 4
    assert "1 duplicate row" in data["summary"]

    data = _apply(session_id, "trim_whitespace")
    data = _apply(session_id, "normalize_column_names")
    names = [c["name"] for c in data["profile"]["columns"]]
    assert "monthly_spend" in names and "order_id" in names

    data = _apply(session_id, "coerce_type", {"column": "monthly_spend", "target": "numeric"})
    assert "failed coercion" in data["summary"]  # "$20.00" and "N/A"

    data = _apply(session_id, "handle_nulls", {"column": "age", "strategy": "median"})
    age = next(c for c in data["profile"]["columns"] if c["name"] == "age")
    assert age["nulls"] == 0

    data = _apply(session_id, "handle_outliers", {"column": "age", "method": "flag"})
    assert "age_is_outlier" in [c["name"] for c in data["profile"]["columns"]]
    assert len(data["pipeline"]) == 6

    # 3. preview: before vs after
    before = client.get(f"/api/sessions/{session_id}/preview", params={"source": "original"})
    after = client.get(f"/api/sessions/{session_id}/preview", params={"source": "current"})
    assert before.status_code == after.status_code == 200
    assert before.json()["total_rows"] == 5
    assert after.json()["total_rows"] == 4
    assert "age_is_outlier" in after.json()["columns"]
    assert "age_is_outlier" not in before.json()["columns"]

    # 4. download cleaned CSV
    download = client.get(f"/api/sessions/{session_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert "attachment" in download.headers["content-disposition"]
    assert download.text.splitlines()[0].startswith("order_id,")

    # 5. download reproducible pipeline JSON
    pipeline = client.get(f"/api/sessions/{session_id}/pipeline")
    assert pipeline.status_code == 200
    payload = json.loads(pipeline.text)
    assert [s["op"] for s in payload["steps"]] == [
        "drop_duplicates",
        "trim_whitespace",
        "normalize_column_names",
        "coerce_type",
        "handle_nulls",
        "handle_outliers",
    ]

    # 6. reset restores the original
    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["profile"]["rows"] == 5
    assert reset.json()["pipeline"] == []


def test_sample_endpoint() -> None:
    response = client.post("/api/sample")
    assert response.status_code == 200
    data = response.json()
    assert data["profile"]["rows"] == 200
    assert data["profile"]["duplicate_rows"] > 0
    assert data["filename"] == "sample_messy.csv"


def test_tsv_upload() -> None:
    tsv = "a\tb\n1\tx\n2\ty\n"
    data = _upload(tsv, filename="data.tsv")
    assert data["profile"]["cols"] == 2
    assert data["profile"]["rows"] == 2


def test_upload_rejects_empty_file() -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
    )
    assert response.status_code == 400


def test_bad_step_returns_400_and_does_not_advance_pipeline() -> None:
    session_id = _upload()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/steps",
        json={"op": "handle_outliers", "params": {"column": "AGE", "method": "explode"}},
    )
    assert response.status_code == 400
    state = client.get(f"/api/sessions/{session_id}/profile").json()
    assert state["pipeline"] == []


def test_unknown_session_is_404() -> None:
    assert client.get("/api/sessions/does-not-exist/profile").status_code == 404
