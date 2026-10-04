"""The scoring code itself is tested with hand-made cases (no model involved)."""
import copy
import json
import re

import pytest

from app import schema as S
from eval import evaluate as E
from eval.paths import GT_PATH

META = {"page": 1, "patient": 1, "set": "clean", "degradation": "none", "language": "fr"}


def gt_page():
    return {"page": 1, "page_type": "delivery", "fields": {
        "delivery_date": {"value": "2026-02-03", "status": "KNOWN", "mark": "handwritten", "font": "A"},
        "birth_weight_g": {"value": 3587, "status": "KNOWN", "mark": "handwritten", "font": "A"},
        "newborn_status": {"value": "Vivant", "status": "KNOWN", "mark": "checkbox"},
        "abnormality": {"value": None, "status": "NOT_PROVIDED", "mark": "blank"},
        "cesarean_indication": {"value": None, "status": "NOT_APPLICABLE", "mark": "dependency"}}, "groups": {}}


def pred(**over):
    base = {"delivery_date": S.fv("2026-02-03", S.KNOWN, 0.9, "ai"), "birth_weight_g": S.fv(3587, S.KNOWN, 0.9, "ai"),
            "newborn_status": S.fv("Vivant", S.KNOWN, 0.9, "ai"), "abnormality": S.fv(None, S.NOT_PROVIDED, 0.9, "ai"),
            "cesarean_indication": S.fv(None, S.NOT_APPLICABLE, 0.9, "ai")}
    base.update(over)
    return {"fields": base, "groups": {}}


def by_path(rows):
    return {r["path"]: r for r in rows}


def test_perfect_prediction_scores_100():
    rows = E.score_page(gt_page(), "delivery", pred(), META)
    assert len(rows) == 5 and all(r["correct"] and r["status_exact"] for r in rows)


def test_wrong_value_hallucination_and_omission_are_counted():
    rows = by_path(E.score_page(gt_page(), "delivery", pred(
        birth_weight_g=S.fv(3581, S.KNOWN, 0.9, "ai"),
        abnormality=S.fv("Aucune", S.KNOWN, 0.9, "ai"),
        delivery_date=S.fv(None, S.ILLEGIBLE, 0.3, "ai")), META))
    assert not rows["birth_weight_g"]["correct"]
    assert rows["abnormality"]["hallucinated"] and not rows["abnormality"]["correct"]
    assert rows["delivery_date"]["omitted"] and not rows["delivery_date"]["correct"]
    assert rows["newborn_status"]["correct"]


def test_blank_status_confusion_is_blank_correct_but_not_status_exact():
    rows = by_path(E.score_page(gt_page(), "delivery", pred(cesarean_indication=S.fv(None, S.NOT_PROVIDED, 0.9, "ai")), META))
    assert rows["cesarean_indication"]["correct"] and not rows["cesarean_indication"]["status_exact"]


def test_needs_review_with_right_value_counts_as_read():
    rows = by_path(E.score_page(gt_page(), "delivery", pred(birth_weight_g=S.fv(3587, S.NEEDS_REVIEW, 0.5, "ai")), META))
    assert rows["birth_weight_g"]["correct"]


def test_misclassified_or_failed_page_scores_zero():
    for ptype, data in (("cover", pred()), (None, None)):
        rows = E.score_page(gt_page(), ptype, data, META)
        assert len(rows) == 5 and not any(r["correct"] for r in rows)


def test_extra_predicted_column_is_a_hallucination():
    g = {"page": 3, "page_type": "pregnancy", "fields": {}, "groups": {"visits": []}}
    p = {"fields": {}, "groups": {"visits": [{"slot": "m9", "fields": {"weight_kg": S.fv(70, S.KNOWN, 0.9, "ai")}}]}}
    rows = E.score_page(g, "pregnancy", p, META)
    assert len(rows) == 1 and rows[0]["hallucinated"]


def test_missing_column_counts_filled_cells_as_omitted():
    g = {"page": 3, "page_type": "pregnancy", "fields": {}, "groups": {"visits": [
        {"slot": "t1_v1", "fields": {"weight_kg": {"value": 60, "status": "KNOWN", "mark": "handwritten"},
                                     "edema": {"value": None, "status": "NOT_PROVIDED", "mark": "blank"}}}]}}
    rows = by_path(E.score_page(g, "pregnancy", {"fields": {"lmp_date": S.fv(None, S.NOT_PROVIDED, 0.9, "ai")}, "groups": {"visits": []}}, META))
    assert rows["visits[t1_v1].weight_kg"]["omitted"] and rows["visits[t1_v1].edema"]["correct"]


def _rows():
    mk = lambda c, ok, filled=True: {"confidence": c, "correct": ok, "pred_status": "KNOWN", "filled": filled}
    return [mk(0.99, True)] * 98 + [mk(0.99, False)] * 2 + [mk(0.6, True)] * 5 + [mk(0.6, False)] * 5


def test_calibration_and_threshold_choice():
    rows = _rows()
    cal = E.calibration(rows)
    top = [b for b in cal["bins"] if b["range"].startswith("0.99")][0]
    assert top["n"] == 100 and top["accuracy"] == pytest.approx(0.98)
    curve = E.threshold_curve(rows)
    at50 = next(c for c in curve if c["threshold"] == 0.5)
    assert at50["silent_errors"] == 7 and at50["follow_up_rate"] == 0 and at50["asked_filled_share"] == 0
    chosen, met = E.choose_threshold(curve, 0.02)
    assert met and chosen["threshold"] == 0.65 and chosen["silent_error_rate"] == pytest.approx(0.02)
    assert chosen["asked_filled_share"] == pytest.approx(10 / 110)
    assert E.decide_threshold(curve, 0.02)["write"] is True
    assert not E.choose_threshold(curve, 0.001)[1]


def test_threshold_is_not_written_when_it_would_ask_too_much_or_cannot_meet_the_target():
    curve = E.threshold_curve(_rows())
    d = E.decide_threshold(curve, 0.02, max_asked=0.05)          # 2% needs 9% asked > 5% cap
    assert d["write"] is False
    kinds = [o["option"][0] for o in d["options"]]
    assert kinds[:2] == ["A", "B"] and "C" in kinds
    b = next(o for o in d["options"] if o["option"].startswith("B"))
    assert b["asked_filled_share"] <= 0.05 and b["silent_error_rate"] > 0.02
    d = E.decide_threshold(curve, 0.001)                         # target unreachable
    assert d["write"] is False and "NOT met" in d["options"][0]["option"]


def test_always_confirmed_fields_count_as_asked_whatever_the_confidence():
    rows = [{"confidence": 1.0, "correct": False, "pred_status": "NEEDS_REVIEW", "filled": True},
            {"confidence": 1.0, "correct": True, "pred_status": "KNOWN", "filled": True}]
    c = E.threshold_curve(rows)[0]
    assert c["asked_filled_share"] == 0.5 and c["silent_errors"] == 0


@pytest.mark.skipif(not GT_PATH.exists(), reason="ground truth not built (python -m eval.build_ground_truth)")
def test_ground_truth_scored_against_itself_is_perfect_and_split_is_by_patient():
    gt = json.loads(GT_PATH.read_text())
    total = 0
    for pg in gt.values():
        data = copy.deepcopy({"fields": pg["fields"], "groups": pg["groups"]})
        for _, _, v in S.iter_fields(pg["page_type"], data):
            v.setdefault("confidence", 1.0)
        rows = E.score_page(pg, pg["page_type"], data, META)
        assert all(r["correct"] for r in rows)
        total += len(rows)
    assert total > 3000
    from eval.split import make_split
    s = make_split()
    assert len(s["pages"]) == 80 and s["duplicates_removed"] == 44
    assert not set(s["dev_patients"]) & set(s["test_patients"])
    for n, m in s["pages"].items():
        assert m["split"] == ("test" if m["patient"] in s["test_patients"] else "dev")


def test_extraction_code_never_touches_ground_truth_or_the_pdf():
    from pathlib import Path
    for f in Path(E.__file__).resolve().parent.parent.joinpath("app").rglob("*.py"):
        src = f.read_text()
        for banned in ("ground_truth", "gt.json", "dossiers_specimen", ".pdf", "maternal_registry_synthetic"):
            assert banned not in src, f"{f.name} mentions {banned}"
        assert not re.search(r"^\s*(from|import)\s+eval\b", src, re.M), f"{f.name} imports eval"
