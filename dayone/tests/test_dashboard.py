"""Bonus dashboard: aggregates only, small groups hidden, nothing individual."""
import importlib
import json

from fastapi.testclient import TestClient

from app import dashboard as D, schema as S


def known(v):
    return S.fv(v, S.KNOWN, 1.0, "midwife")


def record(n, bp=("110/70",), temp=36.8, hiv="Neg", version=1, supersedes=None):
    visits = [{"slot": slot, "fields": {"blood_pressure": known(b), "hiv": known(hiv) if hiv else S.fv(),
                                        "syphilis": S.fv(None, S.NEEDS_REVIEW, 0.4, "ai"), "hbs_antigen": S.fv()}}
              for slot, b in zip(("t1_v1", "t1_v2", "t2_v1"), bp)]
    return {"record_id": f"r{n}", "version": version, "patient_id": f"p{n}", "supersedes": supersedes,
            "linking_code": f"CODE-{n}", "midwife_id": "m",
            "sections": {"pregnancy": {"fields": {}, "groups": {"visits": visits}},
                         "pp_early_mother": {"fields": {"temperature_c": known(temp), "blood_pressure": known("120/80")}},
                         "pp_early_newborn": {"fields": {"temperature_c": S.fv(None, S.ILLEGIBLE, 1.0, "midwife")}}}}


def test_counts_only_known_values_and_only_the_current_version_of_each_registry():
    payloads = [record(i) for i in range(1, 7)]
    payloads.append(record(1, bp=("150/95",), version=2))             # newer version of r1 replaces the older one
    payloads.append(record(9, supersedes="r2"))                       # r9 is a re-digitisation of r2
    out = D.aggregate(payloads, min_cell=1)
    assert out["registries"]["n"] == 6 and out["patients"]["n"] == 6  # r1 once, r2 replaced by r9
    sysb = {b["label"]: b["n"] for b in out["charts"][0]["bins"]}
    assert out["charts"][0]["id"] == "bp_systolic" and sum(sysb.values()) == 12       # 6 visit + 6 postpartum readings
    assert sysb == {"< 100": 0, "100 – 120": 5, "120 – 140": 6, "140 – 160": 1, "≥ 160": 0}
    assert out["charts"][2]["total"]["n"] == 6 and out["charts"][3]["total"]["n"] == 0   # illegible is not a value
    tests = {t["id"]: {c["id"]: c["n"] for c in t["categories"]} for t in out["tests"]}
    assert tests["hiv"] == {"negative": 6, "positive": 0, "other": 0, "none": 0}
    assert tests["syphilis"]["none"] == 6                              # NEEDS_REVIEW is not a result


def test_small_groups_are_hidden_and_zero_stays_zero():
    out = D.aggregate([record(i, hiv="Pos" if i == 1 else "Neg") for i in range(1, 8)], min_cell=5)
    hiv = {c["id"]: c for c in out["tests"][0]["categories"]}
    assert hiv["positive"] == {"id": "positive", "n": None, "shown": "< 5", "suppressed": True}
    # one hidden group could be worked out as "7 registries minus the others": a second group is hidden too
    assert hiv["negative"] == {"id": "negative", "n": None, "shown": "hidden", "suppressed": True, "secondary": True}
    assert hiv["other"]["shown"] == "0" and not hiv["other"]["suppressed"]
    out = D.aggregate([record(i, bp=("110/70", "150/90")) for i in range(1, 7)] + [record(7, bp=("95/60", "165/100"))], min_cell=5)
    shown = [b["shown"] for b in out["charts"][0]["bins"]]              # two small bins: neither can be derived
    assert shown == ["< 5", "6", "7", "6", "< 5"] and not any(b.get("secondary") for b in out["charts"][0]["bins"])
    assert D.aggregate([record(1)], min_cell=5)["registries"] == {"n": None, "shown": "< 5", "suppressed": True}


def test_test_results_are_sorted_by_what_is_written():
    for text, cat in (("Neg", "negative"), ("négatif", "negative"), ("-", "negative"), ("non réactif", "negative"),
                      ("سلبي", "negative"), ("Positif", "positive"), ("+", "positive"), ("Pos", "positive"),
                      ("non fait", "other"), ("en cours", "other")):
        assert D.test_category(text) == cat, text


def test_dashboard_endpoint_returns_no_individual_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYONE_SERVER_DB", str(tmp_path / "srv.db"))
    import app.sync_server as ss
    importlib.reload(ss)
    c = TestClient(ss.app)
    for i in range(1, 7):
        assert c.post("/sync/records", json=record(i)).status_code == 200
    r = c.get("/dashboard/data")
    assert r.status_code == 200 and r.json()["registries"]["n"] == 6
    text = json.dumps(r.json())
    for leak in ("CODE-", "r1", "p1", "110/70", "36.8", "record_id", "patient_id", "linking_code"):
        assert leak not in text, leak
    page = c.get("/dashboard")
    assert page.status_code == 200 and "Tableau de bord" in page.text and "/dashboard/data" in page.text
