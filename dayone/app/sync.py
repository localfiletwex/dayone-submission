"""Sync targets.  The app talks to the server only through `push`."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class SyncUnavailable(Exception):
    """Network problem: not a failure, the record stays queued."""


class SyncRejected(Exception):
    """The server answered and refused the record."""


class HttpSyncTarget:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.url, self.timeout = base_url.rstrip("/") + "/sync/records", timeout

    def push(self, payload: dict) -> str:
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read()).get("result", "stored")
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                raise SyncUnavailable(f"server error {e.code}") from e
            raise SyncRejected(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise SyncUnavailable(type(e).__name__) from e


class InMemorySyncTarget:
    """Idempotent in-process server used by tests."""

    def __init__(self):
        self.received: dict[tuple, dict] = {}
        self.push_count = 0
        self.drop_ack_next = False      # store, then lose the acknowledgement
        self.fail_next = False          # connection drops before the server gets it
        self.reject_next = False

    def push(self, payload: dict) -> str:
        self.push_count += 1
        if self.fail_next:
            self.fail_next = False
            raise SyncUnavailable("connection dropped before upload")
        if self.reject_next:
            self.reject_next = False
            raise SyncRejected("HTTP 422")
        key = (payload["record_id"], payload["version"])
        result = "duplicate" if key in self.received else "stored"
        self.received.setdefault(key, payload)
        if self.drop_ack_next:
            self.drop_ack_next = False
            raise SyncUnavailable("connection dropped before acknowledgement")
        return result
