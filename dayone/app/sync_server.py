"""Simulated central server (a separate process).  Idempotent on
(record_id, version): re-sending the same record never duplicates it.

    uvicorn app.sync_server:app --port 8001

It also serves the bonus dashboard of anonymised aggregates:
    http://127.0.0.1:8001/dashboard          (see app/dashboard.py)
This simulated server has no accounts: a real one must restrict the dashboard
to the roles allowed to see aggregates.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import dashboard

DB = Path(os.environ.get("DAYONE_SERVER_DB", Path(__file__).resolve().parent.parent / "server_data" / "server.db"))
app = FastAPI(title="DayOne sync server (simulated)")
_lock = threading.Lock()


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS records(record_id TEXT, version INTEGER, received_at TEXT DEFAULT CURRENT_TIMESTAMP,"
              " payload TEXT, PRIMARY KEY(record_id, version))")
    return c


@app.post("/sync/records")
def receive(payload: dict):
    if not payload.get("record_id") or not isinstance(payload.get("version"), int):
        raise HTTPException(422, "record_id and version are required")
    if "image" in json.dumps(payload.get("pages", [])).lower() and any("image" in p for p in payload.get("pages", [])):
        raise HTTPException(422, "images must not be synced")
    with _lock, _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO records(record_id, version, payload) VALUES(?,?,?)",
                        (payload["record_id"], payload["version"], json.dumps(payload, ensure_ascii=False)))
        return {"result": "stored" if cur.rowcount else "duplicate"}


@app.get("/sync/records")
def listing():
    with _lock, _db() as c:
        rows = c.execute("SELECT record_id, version, received_at FROM records ORDER BY received_at").fetchall()
    return {"count": len(rows), "records": [{"record_id": r[0], "version": r[1], "received_at": r[2]} for r in rows]}


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/dashboard/data")
def dashboard_data():
    """Aggregated counts only: no record, code, date or identifier leaves this endpoint."""
    with _lock, _db() as c:
        rows = c.execute("SELECT payload FROM records").fetchall()
    return dashboard.aggregate([json.loads(r[0]) for r in rows])
