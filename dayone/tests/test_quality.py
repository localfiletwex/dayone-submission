"""On-device image-quality check (bonus): it warns, it never loses or refuses a photo."""
import glob

import pytest

from app import lifecycle as L, quality as Q
from eval.paths import DEGRADED_DIR, REGISTRY_DIR
from tests.conftest import img, photo_bytes


def test_metrics_flag_blurred_dark_and_tiny_photos_only():
    assert Q.assess(photo_bytes("sharp")) == {"ok": True, "issues": [], "metrics": Q.measure(photo_bytes("sharp"))}
    assert Q.assess(photo_bytes("blurred"))["issues"] == [Q.BLURRED]
    assert Q.assess(photo_bytes("dark"))["issues"] == [Q.DARK]
    assert Q.SMALL in Q.assess(photo_bytes("tiny"))["issues"]
    assert Q.assess(img(1)) is None and Q.assess(b"") is None          # not an image: the check is skipped


@pytest.mark.skipif(not glob.glob(str(REGISTRY_DIR / "*.png")), reason="dataset not in ./data")
def test_no_false_alarm_on_the_provided_pages_and_photos():
    pages = sorted(glob.glob(str(REGISTRY_DIR / "*.png"))) + sorted(glob.glob(str(REGISTRY_DIR / "*.jpg")))
    flagged = [p for p in pages if not Q.assess(open(p, "rb").read())["ok"]]
    assert len(pages) >= 85 and flagged == []


@pytest.mark.skipif(not glob.glob(str(DEGRADED_DIR / "*.jpg")), reason="degraded copies not built (python -m eval.degrade)")
def test_synthetic_degradations_blur_and_low_light_are_caught_shadow_and_skew_pass():
    for kind, expected in (("blur", [Q.BLURRED]), ("lowlight", [Q.DARK]), ("shadow", []), ("skew", [])):
        files = sorted(glob.glob(str(DEGRADED_DIR / f"*-{kind}.jpg")))
        assert files and all(Q.assess(open(f, "rb").read())["issues"] == expected for f in files), kind


def active(rig, rid):
    return rig.store.pages(rid)


def test_doubtful_photo_is_saved_first_then_the_midwife_may_retake_it(rig):
    out = rig.agent.handle_image(rig.mw, photo_bytes("blurred"))
    rid = rig.rid()
    assert len(active(rig, rid)) == 1                                   # saved before anything is asked
    assert "blurred" in out[-1]["text"] and [b["label"] for b in out[-1]["buttons"]] == ["📷 Retake this photo", "Keep it anyway"]
    first = active(rig, rid)[0]["id"]
    rig.press("Retake")
    assert "new photo" in rig.last()["text"] and len(active(rig, rid)) == 1   # the first photo is still there
    out = rig.agent.handle_image(rig.mw, photo_bytes("sharp"))
    pages = active(rig, rid)
    assert len(pages) == 1 and pages[0]["id"] != first                  # replaced, not added
    assert rig.store.page(first)["superseded_by"] == pages[0]["id"]     # the original image is kept, superseded
    assert "replaces the first one" in out[-1]["text"]
    rig.press("Done")
    assert rig.state(rid) == L.PENDING_AI
    kinds = [e["kind"] for e in rig.store.events(rid)]
    assert kinds.count("quality_checked") == 2


def test_keep_anyway_next_photo_or_done_all_keep_the_doubtful_photo(rig):
    rig.agent.handle_image(rig.mw, photo_bytes("dark"))
    rid = rig.rid()
    assert "too dark" in rig.last()["text"]
    rig.press("Keep it anyway")
    assert "I keep this photo" in rig.last()["text"]
    rig.agent.handle_image(rig.mw, photo_bytes("blurred"))              # doubtful again...
    rig.agent.handle_image(rig.mw, photo_bytes("sharp"))                # ...answered by sending the next page
    assert len(active(rig, rid)) == 3 and "Page 3 saved" in rig.last()["text"]
    rig.agent.handle_image(rig.mw, photo_bytes("tiny"))
    rig.say("cmd:done")                                                 # Done without answering: kept
    assert len(active(rig, rid)) == 4 and rig.state(rid) == L.PENDING_AI


def test_quality_events_hold_statistics_only_and_undecodable_bytes_skip_the_check(rig):
    rig.agent.handle_image(rig.mw, photo_bytes("sharp"))
    rig.photo(2)                                                        # the fake bytes used by the other tests
    ev = {e["kind"]: e for e in rig.store.events(rig.rid())}
    assert "Page 2 saved" in rig.last()["text"]
    assert set(__import__("json").loads(ev["quality_checked"]["detail"])) == {
        "ok", "issues", "width", "height", "paper", "sharpness", "ink_contrast"}
    assert "quality_not_checked" in ev


def test_retake_from_the_review_menu_mentions_a_doubtful_new_photo(rig):
    rid = rig.capture(1)
    rig.tick()
    rig.answer_all()
    rig.press("Retake a photo")
    rig.press("Page 1")
    rig.agent.handle_image(rig.mw, photo_bytes("blurred"))
    texts = [m["text"] for m in rig.store.messages(rig.mw["id"])][-2:]
    assert "New photo saved" in texts[0] and "hard to read too" in texts[1] and rig.state(rid) == L.PENDING_AI
