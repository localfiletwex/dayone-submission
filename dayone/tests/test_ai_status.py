"""When the AI SERVICE is in trouble the pages wait, the midwife is told why,
and synchronisation keeps running (it used to wait silently for minutes)."""
import json

from app import lifecycle as L
from app.extractors import base as X
from app.extractors.base import ExtractorUnavailable
from app.extractors.fake import FakeExtractor
from tests.conftest import Rig


class Troubled(FakeExtractor):
    """Fails like a provider in trouble until `ok` is set."""

    def __init__(self, why=X.WHY_OVERLOADED, message="gemini HTTP 503 (the model is overloaded): try again later"):
        super().__init__()
        self.why, self.message, self.ok, self.calls = why, message, False, 0

    def extract(self, image, mime="image/jpeg", page_type_hint=None):
        self.calls += 1
        if not self.ok:
            raise ExtractorUnavailable(self.message, self.why, provider=True)
        return super().extract(image, mime, page_type_hint)


def test_overloaded_service_pages_wait_midwife_is_told_once_and_sync_continues(tmp_path):
    good = Rig(tmp_path / "d")
    done = good.capture(1)                       # a first registry goes all the way to "waiting to sync"
    good.net.online = True
    good.tick()
    good.register()
    assert good.state(done) in (L.REGISTERED, L.SYNCED)
    ex = Troubled()
    good.worker.extractor = good.extractor = ex
    rid = good.capture(2)
    assert good.tick()["processed"] == 0
    rec = good.store.record(rid)
    assert rec["state"] == L.PENDING_AI and rec["proc_attempts"] == 0          # not the page's fault: no attempt used
    assert good.state(done) == L.SYNCED                                        # sync was not blocked by the AI trouble
    w = good.worker
    assert w.ai_problem["why"] == X.WHY_OVERLOADED and "HTTP 503" in w.ai_problem["detail"]
    assert w.ai_why()[0] == X.WHY_OVERLOADED and not w.ai_available()
    bots = [m for m in good.store.messages(good.mw["id"]) if m["sender"] == "bot"]
    msg = bots[-2]                                # the last one is the sync notice of the same tick
    assert "synchronised" in bots[-1]["text"]
    assert "the AI service is overloaded" in msg["text"] and "HTTP 503" in msg["text"] and "Nothing is lost" in msg["text"]
    assert [b["label"] for b in msg["buttons"]] == ["Enter manually now", "Try again now"]
    calls, n_msgs = ex.calls, len(good.store.messages(good.mw["id"]))
    good.tick(); good.tick()                                                   # still waiting: the service is not hammered
    assert ex.calls == calls and len(good.store.messages(good.mw["id"])) == n_msgs
    assert "the AI service is overloaded" in [m for m in good.say("status") if m["sender"] == "bot"][-1]["text"]
    # the service comes back; "Try again now" ends the wait
    ex.ok = True
    good.say("ai:retry_now")
    assert good.tick()["processed"] == 1 and w.ai_problem is None and w.ai_available()
    kinds = [e["kind"] for e in good.store.events()]
    assert "ai_unavailable" in kinds and "ai_available_again" in kinds
    detail = json.loads(next(e for e in good.store.events() if e["kind"] == "ai_unavailable")["detail"])
    assert detail["why"] == X.WHY_OVERLOADED and detail["retry_in"] == 10
    good.store.close()


def test_waiting_time_doubles_up_to_a_minute_and_each_registry_is_told_once(tmp_path):
    r = Rig(tmp_path / "d", extractor=Troubled(X.WHY_RATE_LIMITED, "gemini HTTP 429 (rate limited / quota exhausted)"))
    a = r.capture(1)
    waits = []
    for _ in range(5):
        r.worker.retry_ai_now()
        r.tick()
        waits.append(r.worker.ai_problem["retry_in"])
    assert waits == [10, 20, 40, 60, 60]
    texts = [m["text"] for m in r.store.messages(r.mw["id"]) if m["sender"] == "bot"]
    assert sum("quota or rate limit" in t for t in texts) == 1
    assert r.state(a) == L.PENDING_AI
    r.store.close()


def test_refused_key_and_unknown_model_say_what_must_be_fixed_and_keep_the_pages(tmp_path):
    for why, words in ((X.WHY_KEY_REFUSED, "must fix the key"), (X.WHY_MODEL_UNKNOWN, "must fix the model name")):
        r = Rig(tmp_path / why, extractor=Troubled(why, "gemini HTTP 401 (key rejected): API key not valid"))
        rid = r.capture(1)
        r.tick()
        assert words in r.last()["text"] and r.state(rid) == L.PENDING_AI
        r.press("Enter manually now")                                          # manual entry stays possible
        assert r.state(rid) == L.NEEDS_REVIEW
        r.store.close()


def test_pending_message_says_why_no_key_switched_off_or_offline(tmp_path):
    from app.extractors.gemini import GeminiExtractor
    r = Rig(tmp_path / "nokey", extractor=GeminiExtractor(api_key=""))
    r.capture(1)
    assert "no API key is set on this device" in r.last()["text"] and "Pending AI processing" in r.last()["text"]
    r.store.close()
    r = Rig(tmp_path / "offline", online=False)
    r.capture(1)
    assert "(no connection)" in r.last()["text"]
    r.store.close()
