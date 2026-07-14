"""FastAPI application for the CSV Data Cleaning Pipeline.

Sessions are held in-memory in this process, keyed by a UUID — see the
README for the single-process limitation this implies.
"""

from __future__ import annotations

import csv as csv_module
import io
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, cleaning, profiling

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLE_PATH = BASE_DIR / "data" / "sample_messy.csv"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_SESSIONS = 50
PREVIEW_ROWS = 50
_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """One uploaded dataset plus the cleaning pipeline applied to it."""

    original: pd.DataFrame
    current: pd.DataFrame
    filename: str
    warnings: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.time)


SESSIONS: dict[str, Session] = {}


def _create_session(df: pd.DataFrame, filename: str, warnings: list[str]) -> str:
    """Register a new session, evicting the oldest if over capacity."""
    while len(SESSIONS) >= MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda sid: SESSIONS[sid].created)
        del SESSIONS[oldest]
    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = Session(
        original=df, current=df.copy(), filename=filename, warnings=warnings
    )
    return session_id


def _get_session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session id — upload a file first")
    return session


# ---------------------------------------------------------------------------
# Tolerant parsing
# ---------------------------------------------------------------------------

def parse_table(raw: bytes, filename: str) -> tuple[pd.DataFrame, list[str]]:
    """Parse CSV/TSV bytes tolerantly: sniff encoding & delimiter, skip bad lines."""
    warnings: list[str] = []

    text: str | None = None
    for encoding in _ENCODING_CANDIDATES:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding not in ("utf-8-sig", "utf-8"):
            warnings.append(f"File is not UTF-8; decoded with fallback encoding '{encoding}'")
        break
    if text is None:  # latin-1 accepts any byte, so this should be unreachable
        raise HTTPException(status_code=400, detail="Could not decode file as text")
    if not text.strip():
        raise HTTPException(status_code=400, detail="File is empty")

    if filename.lower().endswith((".tsv", ".tab")):
        sep = "\t"
    else:
        try:
            sep = csv_module.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv_module.Error:
            sep = ","

    bad_lines: list[list[str]] = []

    def _on_bad_line(line: list[str]) -> None:
        bad_lines.append(line)
        return None  # skip the line

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=sep,
            engine="python",
            on_bad_lines=_on_bad_line,
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}") from exc

    if df.shape[1] == 0 or len(df) == 0:
        raise HTTPException(status_code=400, detail="File parsed to an empty table")
    if bad_lines:
        warnings.append(f"Skipped {len(bad_lines)} malformed line(s) during parsing")
    return df, warnings


# ---------------------------------------------------------------------------
# JSON-safe rendering
# ---------------------------------------------------------------------------

def _json_cell(value: Any) -> Any:
    """Convert one DataFrame cell into a JSON-serializable value."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value)
    return str(value)


def _preview_payload(df: pd.DataFrame, limit: int = PREVIEW_ROWS) -> dict[str, Any]:
    head = df.head(limit)
    return {
        "columns": [str(c) for c in df.columns],
        "rows": [
            [_json_cell(v) for v in row]
            for row in head.itertuples(index=False, name=None)
        ],
        "total_rows": int(len(df)),
        "shown_rows": int(len(head)),
    }


def _session_state(session_id: str, session: Session) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "filename": session.filename,
        "warnings": session.warnings,
        "profile": profiling.profile_dataframe(session.current),
        "pipeline": session.steps,
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class StepRequest(BaseModel):
    op: str
    params: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(
    title="CSV Data Cleaning Pipeline",
    version=__version__,
    description="Upload a messy CSV, profile it, build a cleaning pipeline, download the result.",
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a CSV/TSV file and start a cleaning session."""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB)",
        )
    filename = file.filename or "upload.csv"
    df, warnings = parse_table(raw, filename)
    session_id = _create_session(df, filename, warnings)
    return _session_state(session_id, SESSIONS[session_id])


@app.post("/api/sample")
def load_sample() -> dict[str, Any]:
    """Start a session from the bundled sample messy CSV."""
    if not SAMPLE_PATH.exists():
        raise HTTPException(status_code=500, detail="Sample file is missing from the server")
    df, warnings = parse_table(SAMPLE_PATH.read_bytes(), SAMPLE_PATH.name)
    session_id = _create_session(df, SAMPLE_PATH.name, warnings)
    return _session_state(session_id, SESSIONS[session_id])


@app.get("/api/sessions/{session_id}/profile")
def get_profile(session_id: str) -> dict[str, Any]:
    session = _get_session(session_id)
    return _session_state(session_id, session)


@app.get("/api/sessions/{session_id}/preview")
def get_preview(session_id: str, source: str = "current", limit: int = PREVIEW_ROWS) -> dict[str, Any]:
    session = _get_session(session_id)
    if source not in ("current", "original"):
        raise HTTPException(status_code=400, detail="source must be 'current' or 'original'")
    limit = max(1, min(limit, 500))
    df = session.current if source == "current" else session.original
    payload = _preview_payload(df, limit)
    payload["source"] = source
    return payload


@app.post("/api/sessions/{session_id}/steps")
def apply_step(session_id: str, step: StepRequest) -> dict[str, Any]:
    """Apply one cleaning operation and append it to the session pipeline."""
    session = _get_session(session_id)
    try:
        new_df, summary = cleaning.apply_operation(session.current, step.op, step.params)
    except cleaning.CleaningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.current = new_df
    session.steps.append({"op": step.op, "params": step.params, "summary": summary})
    state = _session_state(session_id, session)
    state["summary"] = summary
    return state


@app.post("/api/sessions/{session_id}/reset")
def reset_pipeline(session_id: str) -> dict[str, Any]:
    """Discard all steps and restore the originally uploaded data."""
    session = _get_session(session_id)
    session.current = session.original.copy()
    session.steps = []
    state = _session_state(session_id, session)
    state["summary"] = "Pipeline reset — restored original data"
    return state


@app.get("/api/sessions/{session_id}/download")
def download_csv(session_id: str) -> Response:
    """Download the current (cleaned) data as CSV."""
    session = _get_session(session_id)
    buffer = io.StringIO()
    session.current.to_csv(buffer, index=False)
    stem = Path(session.filename).stem or "data"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem}_cleaned.csv"'},
    )


@app.get("/api/sessions/{session_id}/pipeline")
def download_pipeline(session_id: str) -> JSONResponse:
    """Download the applied pipeline (steps + params) as reproducible JSON."""
    session = _get_session(session_id)
    payload = {
        "source_file": session.filename,
        "tool": "csv-data-cleaner",
        "version": __version__,
        "steps": [{"op": s["op"], "params": s["params"]} for s in session.steps],
    }
    stem = Path(session.filename).stem or "data"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{stem}_pipeline.json"'},
    )


# Mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
