"""Connectivity is cut during capture, during processing and during sync.
In every case: nothing lost, nothing duplicated, states correct."""
import pytest

from app import lifecycle as L
from app.extractors.base import ExtractionError
from app.extractors.fake import FakeExtractor
from tests.conftest import Rig, img


def states(rig, rid):
    return [e["to_state"] for e in rig.store.events(rid) if e["kind"] in ("state_change", "record_created")]


def test_offline_capture_is_stored_and_queued(rig):
    rig.net.online = False
    rid = rig.capture(1, 2)
    assert rig.state(rid) == L.PENDING_AI
    assert "Pending AI processing" in rig.last()["text"]
    assert rig.tick() == {"processed": 0, "synced": 0}
    assert rig.extractor.calls == 0                      # nothing was sent anywhere
    assert [p["status"] for p in rig.store.pages(rid)] == [L.PENDING_AI] * 2


def test_capture_survives_restart_while_offline(rig):
    rig.net.online = False
    rid = rig.capture(1, 2, 3)
    rig.reopen()                                         # app killed and restarted
    assert rig.state(rid) == L.PENDING_AI and len(rig.store.pages(rid)) == 3
    assert rig.store.read_image(rig.store.pages(rid)[0]["id"], rig.mw)[0] == img(1)
    rig.net.online = True
    rig.tick()
    assert rig.state(rid) in (L.NEEDS_REVIEW, L.AI_PROCESSED)


def test_crash_in_the_middle_of_a_capture_loses_nothing_already_saved(rig):
    rig.photo(1)
    rid = rig.rid()
    with pytest.raises(RuntimeError):
        rig.store.add_page(rid, rig.mw["id"], img(2), _fail_after_blob=True)   # died before the row was written
    assert len(rig.store.pages(rid)) == 1                # page 1 intact, page 2 never acknowledged
    rig.photo(2)                                         # the midwife sends it again
    assert [p["seq"] for p in rig.store.pages(rid)] == [1, 2]
    assert len({p["sha256"] for p in rig.store.pages(rid)}) == 2


def test_capture_is_persisted_before_it_is_acknowledged(rig):
    msgs = rig.photo(1)
    page_event = [e["id"] for e in rig.store.events(rig.rid()) if e["kind"] == "page_captured"][0]
    assert msgs and "saved" in msgs[-1]["text"]
    assert rig.store.pages(rig.rid())[0]["status"] == L.CAPTURED and page_event > 0


def test_same_photo_twice_is_not_duplicated(rig):
    rig.photo(1); rig.photo(1)
    assert len(rig.store.pages(rig.rid())) == 1
    assert "same photo" in rig.last()["text"]


class DropsDuringExtract(FakeExtractor):
    """The connection dies while the first page is being processed."""

    def __init__(self, net):
        super().__init__()
        self.net, self.dropped = net, False

    def extract(self, image, mime="image/jpeg", page_type_hint=None):
        out = super().extract(image, mime, page_type_hint)
        if not self.dropped:
            self.dropped, self.net.online = True, False
        return out


def test_connectivity_cut_during_processing(tmp_path):
    rig = Rig(tmp_path / "d")
    rig.extractor = rig.worker.extractor = DropsDuringExtract(rig.net)
    rid = rig.capture(1, 2)
    rig.tick()
    # nothing half-done: still queued, no failure recorded, no retry budget used
    assert rig.state(rid) == L.PENDING_AI
    assert [p["status"] for p in rig.store.pages(rid)] == [L.PENDING_AI] * 2
    assert [p["attempts"] for p in rig.store.pages(rid)] == [0, 0]
    assert L.PROCESSING_FAILED not in states(rig, rid)
    assert rig.store.record(rid)["data"]["sections"] == {}
    rig.reopen()
    rig.net.online = True
    rig.tick()
    assert rig.state(rid) in (L.NEEDS_REVIEW, L.AI_PROCESSED)
    assert all(p["status"] == L.AI_PROCESSED for p in rig.store.pages(rid))
    assert len(rig.store.records()) == 1                 # not duplicated
    assert len([e for e in rig.store.events(rid) if e["kind"] == "page_extracted"]) == 2
    rig.store.close()


def test_partial_progress_is_kept_when_the_connection_drops_between_pages(tmp_path):
    rig = Rig(tmp_path / "d")

    class DropAfterFirst(FakeExtractor):
        def extract(s, image, mime="image/jpeg", page_type_hint=None):
            if s.calls == 1:
                rig.net.online = False
            return super().extract(image, mime, page_type_hint)

    rig.extractor = rig.worker.extractor = DropAfterFirst()
    rid = rig.capture(1, 2)
    rig.tick()
    assert sorted(p["status"] for p in rig.store.pages(rid)) == [L.AI_PROCESSED, L.PENDING_AI]
    assert rig.state(rid) == L.PENDING_AI
    rig.net.online = True
    rig.tick()
    assert rig.extractor.calls == 3                       # page 1 was not processed twice
    assert all(p["status"] == L.AI_PROCESSED for p in rig.store.pages(rid))
    rig.store.close()


def test_processing_failures_retry_then_require_manual_review(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(fail_times=99))
    rid = rig.capture(1)
    for _ in range(3):
        rig.tick()
    assert rig.state(rid) == L.MANUAL_REVIEW_REQUIRED
    assert states(rig, rid) == [L.CAPTURED, L.PENDING_AI, L.PROCESSING_FAILED, L.PENDING_AI, L.PROCESSING_FAILED,
                                L.PENDING_AI, L.PROCESSING_FAILED, L.MANUAL_REVIEW_REQUIRED]
    assert "Enter manually" in [b["label"] for b in rig.last()["buttons"]]
    assert rig.store.read_image(rig.store.pages(rid)[0]["id"], rig.mw)[0] == img(1)   # photo still there
    rig.extractor.fail_times = 0
    rig.press("try again")
    rig.tick()
    assert rig.state(rid) in (L.NEEDS_REVIEW, L.AI_PROCESSED)
    rig.store.close()


def test_one_transient_failure_recovers(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[ExtractionError("bad json")]))
    rid = rig.capture(1)
    rig.tick(); rig.tick()
    assert rig.state(rid) in (L.NEEDS_REVIEW, L.AI_PROCESSED)
    assert states(rig, rid)[:4] == [L.CAPTURED, L.PENDING_AI, L.PROCESSING_FAILED, L.PENDING_AI]
    rig.store.close()


def registered(rig, n=1):
    rid = rig.capture(n)
    rig.tick()
    rig.register()
    assert rig.state(rid) == L.REGISTERED
    return rid


def test_offline_registration_then_sync_on_return(rig):
    rid = rig.capture(1); rig.tick()
    rig.net.online = False
    rig.register()
    assert rig.state(rid) == L.REGISTERED and "will be synchronised" in rig.last()["text"]
    assert rig.tick()["synced"] == 0 and rig.server.push_count == 0
    rig.reopen()
    rig.net.online = True
    assert rig.tick()["synced"] == 1
    assert rig.state(rid) == L.SYNCED and len(rig.server.received) == 1
    assert rig.tick()["synced"] == 0 and rig.server.push_count == 1      # not sent twice


def test_connectivity_cut_during_sync_before_upload(rig):
    rid = registered(rig)
    rig.server.fail_next = True
    rig.tick()
    assert rig.state(rid) == L.REGISTERED and len(rig.server.received) == 0
    assert L.SYNC_FAILED not in states(rig, rid)          # losing the network is not a failure
    rig.tick()
    assert rig.state(rid) == L.SYNCED and len(rig.server.received) == 1


def test_connectivity_cut_during_sync_after_upload_does_not_duplicate(rig):
    rid = registered(rig)
    rig.server.drop_ack_next = True                       # server stored it, the ack was lost
    rig.tick()
    assert rig.state(rid) == L.REGISTERED and len(rig.server.received) == 1
    assert len(rig.store.outbox("pending")) == 1
    rig.tick()
    assert rig.state(rid) == L.SYNCED
    assert len(rig.server.received) == 1 and rig.server.push_count == 2


def test_toggle_goes_offline_while_the_upload_is_in_flight(rig):
    rid = registered(rig)
    orig = rig.server.push

    def push(payload):
        r = orig(payload)
        rig.net.online = False
        return r
    rig.server.push = push
    rig.tick()
    assert rig.state(rid) == L.REGISTERED
    rig.server.push = orig
    rig.net.online = True
    rig.tick()
    assert rig.state(rid) == L.SYNCED and len(rig.server.received) == 1


def test_server_rejection_goes_to_sync_failed_and_retries(rig):
    rid = registered(rig)
    rig.server.reject_next = True
    rig.tick()
    assert states(rig, rid)[-2:] == [L.SYNC_FAILED, L.REGISTERED]
    rig.tick()
    assert rig.state(rid) == L.SYNCED and len(rig.server.received) == 1


def test_repeated_rejection_stays_sync_failed_until_manual_retry(rig):
    rid = registered(rig)
    for _ in range(5):
        rig.server.reject_next = True
        rig.tick()
    assert rig.state(rid) == L.SYNC_FAILED
    assert rig.tick()["synced"] == 0
    rig.press("sync again")
    rig.tick()
    assert rig.state(rid) == L.SYNCED and len(rig.server.received) == 1


def test_no_record_lost_or_duplicated_under_flapping_connectivity(rig):
    rids = []
    for i in range(1, 9):
        rig.net.online = i % 3 != 0
        rids.append(rig.capture(i))
        rig.tick()
        if i % 2:
            rig.reopen()
    rig.net.online = True
    rig.tick()
    for rid in rids:                                       # review + match every one
        rig.say(f"review:{rid}")
        rig.net.online = rid != rids[2]
        rig.register()
        rig.server.drop_ack_next = rid == rids[4]
        rig.tick()
    rig.net.online = True
    rig.tick(); rig.tick()
    assert len(rig.store.records()) == 8
    assert [rig.state(r) for r in rids] == [L.SYNCED] * 8
    assert sorted(k[0] for k in rig.server.received) == sorted(rids)
    assert len(rig.server.received) == 8
    assert all(len(rig.store.pages(r)) == 1 for r in rids)


def test_sync_payload_never_contains_images(rig):
    registered(rig); rig.tick()
    payload = next(iter(rig.server.received.values()))
    assert set(payload["pages"][0]) == {"page_id", "seq", "page_type", "sha256", "captured_at", "status"}
    assert b"FAKEJPEG" not in repr(payload).encode()
