"""Offline-first queue worker.

Two queues, both persisted in the encrypted database:
* "Pending AI processing": records in PENDING_AI (and their pages);
* outbox: REGISTERED records waiting to be synchronised.

`tick()` is safe to call at any time and after any crash: a page stays
PENDING_AI until its result is committed, and an outbox row stays pending
until the server has acknowledged it (the server is idempotent, so a lost
acknowledgement cannot create a duplicate).  Losing the network is never a
failure: nothing changes state and nothing counts against a retry budget.

The same holds when the AI SERVICE is in trouble (overloaded, quota used up,
key refused): the pages stay queued, the worker waits before asking again
(`ai_problem`), tells the midwife once per registry why she is waiting, and
keeps synchronising meanwhile.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, lifecycle as L, linking, pipeline
from .extractors import base as X
from .extractors.base import ExtractionError, Extractor, ExtractorUnavailable
from .redaction import get_logger
from .storage import Store, now
from .sync import SyncRejected, SyncUnavailable

log = get_logger("dayone.worker")


class Connectivity:
    """Simulated network state (the demo toggle)."""

    def __init__(self, online: bool = True):
        self.online = online


def build_sync_payload(store: Store, rid: str) -> dict:
    """What leaves the device: structured data only.  Images never do."""
    r = store.record(rid)
    return {"record_id": r["id"], "version": r["version"], "patient_id": r["patient_id"],
            "midwife_id": r["midwife_id"], "created_at": r["created_at"], "supersedes": r["supersedes"],
            "linking_code": r["linking_code"], "sections": r["data"]["sections"],
            "pages": [{"page_id": p["id"], "seq": p["seq"], "page_type": p["page_type"], "sha256": p["sha256"],
                       "captured_at": p["captured_at"], "status": p["status"]} for p in store.pages(rid)]}


class Worker:
    def __init__(self, store: Store, extractor: Extractor, sync_target, connectivity: Connectivity | None = None,
                 threshold: float | None = None, notifier=None, threshold_arabic: float | None = None,
                 arabic_scope: str = "numbers"):
        self.store, self.extractor, self.sync_target = store, extractor, sync_target
        self.connectivity = connectivity or Connectivity()
        self.threshold = threshold if threshold is not None else config.load_threshold()["value"]
        # optional stricter threshold on pages with Arabic entries, for their numbers or for every field
        self.threshold_arabic, self.arabic_scope = threshold_arabic, arabic_scope
        # object with on_processed / on_failed / on_synced / on_sync_failed / on_ai_waiting
        self.notifier = notifier
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # Trouble with the AI service (not with a page).  None when all is well, else
        # {"why": <X.WHY_* code>, "detail": str, "since": iso time, "retry_in": seconds}.
        self.ai_problem: dict | None = None
        self._ai_failures = 0               # consecutive troubled calls, drives the waiting time
        self._ai_retry_at = 0.0             # time.monotonic() before which the AI is not asked again
        self._told: set[str] = set()        # records whose midwife was told about the current trouble

    # ---- helpers --------------------------------------------------------
    def ai_available(self) -> bool:
        return self.connectivity.online and self.extractor.available() and time.monotonic() >= self._ai_retry_at

    def ai_why(self) -> tuple[str, str]:
        """(code, detail): why the AI cannot be used right now; ("", "") when it can.
        The code is one of extractors.base.WHY_CODES; the chat translates it."""
        if not self.connectivity.online:
            return X.WHY_OFFLINE, ""
        if not self.extractor.available():
            return self.extractor.why_unavailable() or X.WHY_SWITCHED_OFF, ""
        if self.ai_problem and time.monotonic() < self._ai_retry_at:
            return self.ai_problem["why"], self.ai_problem["detail"]
        return "", ""

    def retry_ai_now(self) -> None:
        """Stop waiting and ask the AI service again at the next tick."""
        self._ai_retry_at = 0.0

    def _ai_trouble(self, rid, e: ExtractorUnavailable) -> None:
        """The AI service did not do the work.  Wait before asking again, make the
        reason visible (state, audit trail) and tell the midwife once."""
        self._ai_failures += 1
        wait = min(config.AI_RETRY_FIRST_SECONDS * 2 ** (self._ai_failures - 1), config.AI_RETRY_MAX_SECONDS)
        self._ai_retry_at = time.monotonic() + wait
        since = self.ai_problem["since"] if self.ai_problem else now()
        self.ai_problem = {"why": e.why or X.WHY_NETWORK, "detail": str(e)[:300], "since": since, "retry_in": wait}
        self.store.event("ai_unavailable", record_id=rid, why=self.ai_problem["why"],
                         detail=self.ai_problem["detail"], retry_in=wait)
        if rid not in self._told:
            self._told.add(rid)
            self._notify("on_ai_waiting", rid)

    def _ai_recovered(self) -> None:
        if self.ai_problem:
            self.store.event("ai_available_again", why=self.ai_problem["why"])
        self.ai_problem, self._ai_failures, self._ai_retry_at = None, 0, 0.0
        self._told.clear()

    def _notify(self, name, rid):
        handler = getattr(self.notifier, name, None)       # a notifier may ignore some notifications
        if handler is not None:
            handler(rid)

    # ---- processing queue -----------------------------------------------
    def process_pending(self) -> int:
        done = 0
        for r in self.store.records("state=?", (L.PENDING_AI,)):
            if r["next_attempt_at"] and r["next_attempt_at"] > now():
                continue
            if not self.ai_available():
                break
            done += self._process_record(r["id"])
        return done

    def _process_record(self, rid) -> int:
        store = self.store
        for page in store.pages(rid):
            if page["status"] != L.PENDING_AI:
                continue
            if not self.ai_available():
                return 0
            try:
                image = store.image_for_processing(page["id"])
                raw = self.extractor.extract(image, page["mime"], page["page_type"])
                if not self.connectivity.online:          # connection dropped while the call was in flight
                    raise ExtractorUnavailable("connection lost during processing", X.WHY_OFFLINE)
                self._ai_recovered()
                ptype, section, info = pipeline.postprocess(raw, self.threshold, self.threshold_arabic, self.arabic_scope)
            except ExtractorUnavailable as e:
                log.info("record %s: AI unavailable (%s); stays queued", rid[:8], e)
                if e.provider:
                    self._ai_trouble(rid, e)
                return 0
            except (ExtractionError, ValueError) as e:
                self._processing_failed(rid, page, str(e))
                return 0
            with store.tx():
                rec = store.record(rid)
                if rec["state"] != L.PENDING_AI:       # the midwife took over meanwhile
                    return 0
                data = rec["data"]
                data["sections"][ptype] = pipeline.merge_section(data["sections"].get(ptype), section)
                store.update_record(rid, data=data)
                store.set_page(page["id"], status=L.AI_PROCESSED, page_type=ptype, error=None)
                store.event("page_extracted", record_id=rid, page_id=page["id"], page_type=ptype,
                            extractor=self.extractor.name, dropped_keys=len(info["dropped_keys"]),
                            redactions=info["redactions"])
        with store.tx():
            rec = store.record(rid)
            if rec["state"] != L.PENDING_AI or any(p["status"] != L.AI_PROCESSED for p in store.pages(rid)):
                return 0
            code = linking.code_from_record(rec["data"])
            store.update_record(rid, linking_code=code, proc_attempts=0, next_attempt_at=None)
            store.transition(rid, L.AI_PROCESSED, reason="all pages extracted")
            dup = self.find_duplicate(rid, linking.code_from_record(rec["data"], tentative=True))
            n_unc = sum(len(pipeline.uncertain_paths(k, s)) for k, s in rec["data"]["sections"].items())
            if dup:
                store.transition(rid, L.DUPLICATE_SUSPECTED, reason="same code as an existing record",
                                 existing_record=dup["id"])
            elif n_unc:
                store.transition(rid, L.NEEDS_REVIEW, reason=f"{n_unc} uncertain fields")
        self._notify("on_processed", rid)
        return 1

    def find_duplicate(self, rid, code):
        ncode = linking.normalize_code(code)
        if not ncode:
            return None
        for other in self.store.records("id<>? AND superseded_by IS NULL AND state IN (?,?,?)",
                                        (rid, L.REGISTERED, L.SYNCED, L.SYNC_FAILED)):
            if linking.normalize_code(other["linking_code"]) == ncode:
                return other
        return None

    def _processing_failed(self, rid, page, reason):
        store = self.store
        with store.tx():
            attempts = page["attempts"] + 1
            # The reason is technical text about the request (never page content); keep enough of it
            # for the provider's own explanation to stay readable.
            store.set_page(page["id"], attempts=attempts, error=reason[:400])
            store.transition(rid, L.PROCESSING_FAILED, reason=reason[:400], attempt=attempts)
            if attempts < config.MAX_PROCESSING_ATTEMPTS:
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=2 ** attempts)).isoformat(timespec="milliseconds")
                store.update_record(rid, proc_attempts=attempts, next_attempt_at=retry_at)
                store.transition(rid, L.PENDING_AI, reason="automatic retry")
                final = False
            else:
                store.set_page(page["id"], status=L.PROCESSING_FAILED)
                store.update_record(rid, proc_attempts=attempts, next_attempt_at=None)
                store.transition(rid, L.MANUAL_REVIEW_REQUIRED, reason=f"AI failed {attempts} times: {reason[:300]}")
                final = True
        if final:
            self._notify("on_failed", rid)

    # ---- sync queue -----------------------------------------------------
    def sync_pending(self) -> int:
        sent = 0
        for row in self.store.outbox("pending"):
            if not self.connectivity.online:
                break
            rec = self.store.record(row["record_id"])
            if rec["state"] != L.REGISTERED:
                continue
            if rec["next_attempt_at"] and rec["next_attempt_at"] > now():
                continue
            try:
                payload = build_sync_payload(self.store, rec["id"])
                result = self.sync_target.push(payload)
                if not self.connectivity.online:
                    raise SyncUnavailable("connection lost during sync")
            except SyncUnavailable as e:
                log.info("record %s: sync postponed (%s)", rec["id"][:8], e)
                break
            except SyncRejected as e:
                self._sync_failed(row, rec, str(e))
                continue
            with self.store.tx():
                self.store.set_outbox(row["id"], status="sent")
                self.store.update_record(rec["id"], sync_attempts=0, next_attempt_at=None)
                self.store.transition(rec["id"], L.SYNCED, reason=f"server: {result}")
            sent += 1
            self._notify("on_synced", rec["id"])
        return sent

    def _sync_failed(self, row, rec, reason):
        attempts = row["attempts"] + 1
        with self.store.tx():
            self.store.set_outbox(row["id"], attempts=attempts, last_error=reason[:200])
            self.store.transition(rec["id"], L.SYNC_FAILED, reason=reason[:200], attempt=attempts)
            if attempts < config.MAX_SYNC_ATTEMPTS:
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=2 ** attempts)).isoformat(timespec="milliseconds")
                self.store.update_record(rec["id"], sync_attempts=attempts, next_attempt_at=retry_at)
                self.store.transition(rec["id"], L.REGISTERED, reason="automatic retry")
                return
        self._notify("on_sync_failed", rec["id"])

    def retry_sync(self, rid):
        """Manual retry of a record stuck in SYNC_FAILED."""
        with self.store.tx():
            self.store.update_record(rid, next_attempt_at=None)
            self.store.transition(rid, L.REGISTERED, reason="manual retry")
            for row in self.store.q("SELECT id FROM outbox WHERE record_id=? AND status='pending'", (rid,)):
                self.store.set_outbox(row["id"], attempts=0)

    # ---- loop -----------------------------------------------------------
    def tick(self) -> dict:
        with self._lock:
            return {"processed": self.process_pending(), "synced": self.sync_pending()}

    def run_forever(self, interval: float = 1.0):
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception:                      # keep the worker alive; details stay out of logs
                log.exception("worker tick failed")

    def stop(self):
        self._stop.set()
