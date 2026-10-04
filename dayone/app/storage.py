"""Encrypted local storage: SQLCipher database + AES-GCM image blobs.

Guarantees this module is responsible for:
* everything on disk is encrypted (database by SQLCipher, images by AES-GCM
  with a random data key that itself lives inside the encrypted database);
* a capture is durably written BEFORE any processing and before it is
  acknowledged (`add_page`);
* every state change is written, together with an append-only event, in one
  transaction (`transition`), and invalid transitions are refused;
* nothing here needs the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import sqlcipher3

from . import crypto, lifecycle as L

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patients(
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  linking_code TEXT, summary TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS records(
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, midwife_id TEXT NOT NULL,
  state TEXT NOT NULL, patient_id TEXT, linking_code TEXT, version INTEGER NOT NULL DEFAULT 1,
  supersedes TEXT, superseded_by TEXT, data TEXT NOT NULL DEFAULT '{"sections":{}}',
  proc_attempts INTEGER NOT NULL DEFAULT 0, sync_attempts INTEGER NOT NULL DEFAULT 0,
  failure_reason TEXT, next_attempt_at TEXT);
CREATE TABLE IF NOT EXISTS pages(
  id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), seq INTEGER NOT NULL,
  captured_at TEXT NOT NULL, midwife_id TEXT NOT NULL, sha256 TEXT NOT NULL, blob_name TEXT NOT NULL,
  mime TEXT NOT NULL, status TEXT NOT NULL, page_type TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, record_id TEXT, page_id TEXT,
  actor TEXT, kind TEXT NOT NULL, from_state TEXT, to_state TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  UNIQUE(record_id, version));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, midwife_id TEXT NOT NULL, sender TEXT NOT NULL,
  text TEXT NOT NULL, buttons TEXT NOT NULL DEFAULT '[]', record_id TEXT, page_id TEXT);
CREATE TABLE IF NOT EXISTS sessions(midwife_id TEXT PRIMARY KEY, state TEXT NOT NULL);
-- WhatsApp message ids already handled (stored hashed: the raw id contains the sender's phone number)
CREATE TABLE IF NOT EXISTS wa_seen(id_hash TEXT PRIMARY KEY, ts TEXT NOT NULL);
-- events are append-only
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""

ROLES = ("midwife", "supervisor", "data_manager")
# who may open an original registry image
IMAGE_ROLES = ("midwife", "supervisor")
DEFAULT_USERS = (("midwife1", "midwife"), ("midwife2", "midwife"),
                 ("supervisor1", "supervisor"), ("data1", "data_manager"))


class WrongKey(Exception):
    pass


class AccessDenied(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    """Random internal identifier; never derived from personal information."""
    return uuid.uuid4().hex


class Store:
    def __init__(self, directory: Path, pin: str):
        self.dir = Path(directory)
        self.blob_dir = self.dir / "blobs"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        key = crypto.derive_key(pin, crypto.load_or_create_salt(self.dir / "salt"))
        self._lock = threading.RLock()
        self._depth = 0
        self.db = sqlcipher3.connect(str(self.dir / "dayone.db"), check_same_thread=False,
                                     isolation_level=None)
        self.db.row_factory = sqlcipher3.Row
        try:
            self.db.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            self.db.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError as e:
            self.db.close()
            raise WrongKey("wrong PIN or corrupted database") from e
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = FULL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        with self.tx():
            if self.meta("blob_key") is None:
                self.set_meta("blob_key", os.urandom(32).hex())
            for username, role in DEFAULT_USERS:
                self.db.execute("INSERT OR IGNORE INTO users(id, username, role) VALUES(?,?,?)",
                                (new_id(), username, role))
        self._blob_key = bytes.fromhex(self.meta("blob_key"))

    def close(self):
        self.db.close()

    # ---- transactions ---------------------------------------------------
    @contextmanager
    def tx(self):
        """Re-entrant transaction: commits only when the outermost block exits."""
        with self._lock:
            if self._depth == 0:
                self.db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.db.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.db.execute("COMMIT")

    def q(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    # ---- meta / users ---------------------------------------------------
    def meta(self, key):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else None

    def set_meta(self, key, value):
        with self.tx():
            self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(value)))

    def user(self, username):
        return self.one("SELECT * FROM users WHERE username=?", (username,))

    def user_by_id(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    # ---- events ---------------------------------------------------------
    def event(self, kind, record_id=None, page_id=None, actor=None, from_state=None, to_state=None, **detail):
        """Append an audit event.  `detail` must never contain field VALUES."""
        with self.tx():
            self.db.execute(
                "INSERT INTO events(ts, record_id, page_id, actor, kind, from_state, to_state, detail) VALUES(?,?,?,?,?,?,?,?)",
                (now(), record_id, page_id, actor, kind, from_state, to_state, json.dumps(detail)))

    def events(self, record_id=None):
        if record_id:
            return self.q("SELECT * FROM events WHERE record_id=? ORDER BY id", (record_id,))
        return self.q("SELECT * FROM events ORDER BY id")

    # ---- records --------------------------------------------------------
    def create_record(self, midwife_id) -> str:
        rid = new_id()
        with self.tx():
            t = now()
            self.db.execute("INSERT INTO records(id, created_at, updated_at, midwife_id, state) VALUES(?,?,?,?,?)",
                            (rid, t, t, midwife_id, L.CAPTURED))
            self.event("record_created", record_id=rid, actor=midwife_id, to_state=L.CAPTURED)
        return rid

    def record(self, rid):
        r = self.one("SELECT * FROM records WHERE id=?", (rid,))
        if r:
            r["data"] = json.loads(r["data"])
        return r

    def records(self, where="1=1", args=()):
        rows = self.q(f"SELECT * FROM records WHERE {where} ORDER BY created_at", args)
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def transition(self, rid, to_state, actor="system", reason="", **detail):
        """Validate and persist a state change + its event atomically."""
        with self.tx():
            r = self.one("SELECT state FROM records WHERE id=?", (rid,))
            if r is None:
                raise KeyError(rid)
            L.check_transition(r["state"], to_state)
            self.db.execute("UPDATE records SET state=?, updated_at=?, failure_reason=? WHERE id=?",
                            (to_state, now(), reason if to_state in L.FAILURE_STATES else None, rid))
            self.event("state_change", record_id=rid, actor=actor, from_state=r["state"], to_state=to_state,
                       reason=reason, **detail)
        return to_state

    def update_record(self, rid, **cols):
        allowed = {"data", "patient_id", "linking_code", "version", "supersedes", "superseded_by",
                   "proc_attempts", "sync_attempts", "failure_reason", "next_attempt_at"}
        assert set(cols) <= allowed, set(cols) - allowed
        if "data" in cols:
            cols["data"] = json.dumps(cols["data"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.tx():
            self.db.execute(f"UPDATE records SET {sets}, updated_at=? WHERE id=?", (*cols.values(), now(), rid))

    # ---- pages / images -------------------------------------------------
    def add_page(self, rid, midwife_id, image: bytes, mime="image/jpeg", _fail_after_blob=False):
        """Persist a captured image.  Returns (page_id, is_duplicate_in_record).

        Order matters for 'no record lost': the encrypted blob is written and
        fsynced first, then the row and event are committed.  Only after this
        returns may the capture be acknowledged to the midwife or processed.
        """
        sha = hashlib.sha256(image).hexdigest()
        dup = self.one("SELECT id FROM pages WHERE record_id=? AND sha256=? AND superseded_by IS NULL", (rid, sha))
        if dup:
            return dup["id"], True
        pid = new_id()
        blob = crypto.encrypt_blob(self._blob_key, image, pid.encode())
        tmp = self.blob_dir / f"{pid}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.blob_dir / f"{pid}.bin")
        if _fail_after_blob:                      # test hook: crash between blob and row
            raise RuntimeError("simulated crash during capture")
        with self.tx():
            seq = self.one("SELECT COALESCE(MAX(seq),0)+1 AS n FROM pages WHERE record_id=?", (rid,))["n"]
            self.db.execute(
                "INSERT INTO pages(id, record_id, seq, captured_at, midwife_id, sha256, blob_name, mime, status) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, rid, seq, now(), midwife_id, sha, f"{pid}.bin", mime, L.CAPTURED))
            self.event("page_captured", record_id=rid, page_id=pid, actor=midwife_id, to_state=L.CAPTURED, seq=seq)
        return pid, False

    def page(self, pid):
        return self.one("SELECT * FROM pages WHERE id=?", (pid,))

    def pages(self, rid, active_only=True):
        cond = " AND superseded_by IS NULL" if active_only else ""
        return self.q(f"SELECT * FROM pages WHERE record_id=?{cond} ORDER BY seq", (rid,))

    def set_page(self, pid, **cols):
        allowed = {"status", "page_type", "attempts", "superseded_by", "error"}
        assert set(cols) <= allowed
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.tx():
            p = self.page(pid)
            self.db.execute(f"UPDATE pages SET {sets} WHERE id=?", (*cols.values(), pid))
            if "status" in cols and cols["status"] != p["status"]:
                self.event("page_status", record_id=p["record_id"], page_id=pid,
                           from_state=p["status"], to_state=cols["status"])

    def read_image(self, pid, user: dict) -> tuple[bytes, str]:
        """Decrypt an original image, enforcing role-based access and auditing it."""
        p = self.page(pid)
        if p is None:
            raise KeyError(pid)
        if user["role"] not in IMAGE_ROLES or (user["role"] == "midwife" and p["midwife_id"] != user["id"]):
            self.event("image_access_denied", record_id=p["record_id"], page_id=pid, actor=user["id"])
            raise AccessDenied("this role may not open original images")
        self.event("image_viewed", record_id=p["record_id"], page_id=pid, actor=user["id"])
        return self._decrypt(p), p["mime"]

    def _decrypt(self, p) -> bytes:
        return crypto.decrypt_blob(self._blob_key, (self.blob_dir / p["blob_name"]).read_bytes(), p["id"].encode())

    def image_for_processing(self, pid) -> bytes:
        """Internal read used by the extraction worker (no user involved)."""
        return self._decrypt(self.page(pid))

    # ---- patients -------------------------------------------------------
    def create_patient(self, created_by, linking_code, summary) -> str:
        pid = new_id()
        with self.tx():
            self.db.execute("INSERT INTO patients(id, created_at, created_by, linking_code, summary) VALUES(?,?,?,?,?)",
                            (pid, now(), created_by, linking_code, json.dumps(summary)))
            self.event("patient_created", actor=created_by, patient_id=pid)
        return pid

    def patients(self):
        rows = self.q("SELECT * FROM patients ORDER BY created_at")
        for r in rows:
            r["summary"] = json.loads(r["summary"])
        return rows

    def patient(self, pid):
        r = self.one("SELECT * FROM patients WHERE id=?", (pid,))
        if r:
            r["summary"] = json.loads(r["summary"])
        return r

    def update_patient(self, pid, linking_code, summary):
        with self.tx():
            self.db.execute("UPDATE patients SET linking_code=?, summary=? WHERE id=?",
                            (linking_code, json.dumps(summary), pid))

    # ---- outbox ---------------------------------------------------------
    def enqueue_sync(self, rid, version):
        with self.tx():
            self.db.execute("INSERT OR IGNORE INTO outbox(record_id, version) VALUES(?,?)", (rid, version))

    def outbox(self, status="pending"):
        return self.q("SELECT * FROM outbox WHERE status=? ORDER BY id", (status,))

    def set_outbox(self, oid, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.tx():
            self.db.execute(f"UPDATE outbox SET {sets} WHERE id=?", (*cols.values(), oid))

    # ---- chat -----------------------------------------------------------
    def add_message(self, midwife_id, sender, text, buttons=(), record_id=None, page_id=None) -> int:
        with self.tx():
            cur = self.db.execute(
                "INSERT INTO messages(ts, midwife_id, sender, text, buttons, record_id, page_id) VALUES(?,?,?,?,?,?,?)",
                (now(), midwife_id, sender, text, json.dumps(list(buttons), ensure_ascii=False), record_id, page_id))
            return cur.lastrowid

    def messages(self, midwife_id, after=0):
        rows = self.q("SELECT * FROM messages WHERE midwife_id=? AND id>? ORDER BY id", (midwife_id, after))
        for r in rows:
            r["buttons"] = json.loads(r["buttons"])
        return rows

    def last_message_id(self, midwife_id) -> int:
        return self.one("SELECT COALESCE(MAX(id),0) AS m FROM messages WHERE midwife_id=?", (midwife_id,))["m"]

    def wa_first_time(self, message_id: str) -> bool:
        """True the first time a WhatsApp message id is seen (Meta re-delivers webhooks)."""
        if not message_id:
            return True
        with self.tx():
            cur = self.db.execute("INSERT OR IGNORE INTO wa_seen(id_hash, ts) VALUES(?,?)",
                                  (hashlib.sha256(message_id.encode()).hexdigest(), now()))
            return cur.rowcount == 1

    def session(self, midwife_id) -> dict:
        r = self.one("SELECT state FROM sessions WHERE midwife_id=?", (midwife_id,))
        return json.loads(r["state"]) if r else {"mode": "idle"}

    def set_session(self, midwife_id, state: dict):
        with self.tx():
            self.db.execute("INSERT INTO sessions(midwife_id, state) VALUES(?,?) ON CONFLICT(midwife_id) DO UPDATE SET state=excluded.state",
                            (midwife_id, json.dumps(state, ensure_ascii=False)))
