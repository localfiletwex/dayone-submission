from app import lifecycle as L, linking
from app.extractors.fake import FakeExtractor
from tests.conftest import Rig


def P(pid, code, age=None, province=None, lmp=None):
    return {"id": pid, "linking_code": code, "summary": {"age": age, "province": province, "lmp_date": lmp}}


def test_exact_and_near_codes_are_only_candidates():
    pats = [P("a", "2026-823-001", 31, "Kénitra"), P("b", "2026-823-007", 22, "Rabat"), P("c", "1999-111-999")]
    c = linking.candidates(pats, "2026 823 001", {"age": 31, "province": "Kenitra", "lmp_date": None})
    assert [x["patient"]["id"] for x in c] == ["a", "b"]
    assert c[0]["exact_code"] and "same code" in c[0]["reasons"] and "similar code" in c[1]["reasons"]


def test_demographics_alone_need_two_agreements():
    pats = [P("a", None, 31, "Kénitra", "2025-04-26"), P("b", None, 31, "Rabat", "2024-01-01")]
    s = {"age": 31, "province": "Kénitra", "lmp_date": "2025-04-26"}
    assert [x["patient"]["id"] for x in linking.candidates(pats, None, s)] == ["a"]
    assert linking.candidates(pats, None, {"age": 31, "province": None, "lmp_date": None}) == []


def test_unrelated_code_is_not_a_candidate():
    assert linking.candidates([P("a", "ABCD-1")], "ZZZZ-9", {}) == []


def test_normalize_code():
    assert linking.normalize_code(" 2026-823-001 ") == "2026823001" == linking.normalize_code("2026 823 001")


def cover(code, conf=0.99):
    return {"page_type": "cover", "groups": {}, "fields": {
        "fiche_number": [code, "KNOWN", conf], "region": ["R", "KNOWN", 0.99], "province": ["Prov", "KNOWN", 0.99],
        "facility_name": ["CS", "KNOWN", 0.99], "facility_type": ["CSU", "KNOWN", 0.99],
        "coverage_mode": ["Fixe", "KNOWN", 0.99], "high_risk_pregnancy": [False, "KNOWN", 0.99],
        "risk_types": [[], "KNOWN", 0.99]}}


def to_validated(rig, n):
    rid = rig.capture(n)
    rig.tick()
    if rig.state(rid) == L.DUPLICATE_SUSPECTED:
        rig.press("different woman")
    rig.answer_all()
    rig.press("Confirm")
    return rid


def test_no_patient_is_ever_created_without_the_midwife(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111")]))
    rid = to_validated(rig, 1)
    assert rig.state(rid) == L.VALIDATED and rig.store.patients() == []
    labels = [b["label"] for b in rig.last()["buttons"]]
    assert labels == ["Create new patient", "I'm not sure"]
    rig.tick(); rig.reopen(); rig.tick()                 # time passes, app restarts: still nothing created
    assert rig.store.patients() == [] and rig.state(rid) == L.VALIDATED
    rig.press("Create new")
    assert len(rig.store.patients()) == 1 and rig.state(rid) == L.REGISTERED
    rig.store.close()


def test_exact_code_match_is_still_a_question_with_the_four_choices(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111"), cover("BBB-222"), cover("AAA-112")]))
    r1 = to_validated(rig, 1); rig.press("Create new")
    r2 = to_validated(rig, 2); rig.press("Create new")
    r3 = to_validated(rig, 3)                             # near-identical to AAA-111
    m = rig.last()
    assert [b["label"] for b in m["buttons"]] == ["Patient 1", "None, create new", "I'm not sure"]
    assert "similar code" in m["text"] and "you decide" in m["text"]
    assert rig.state(r3) == L.VALIDATED and rig.store.record(r3)["patient_id"] is None
    assert len(rig.store.patients()) == 2
    rig.press("Patient 1")
    assert rig.store.record(r3)["patient_id"] == rig.store.record(r1)["patient_id"]
    assert len(rig.store.patients()) == 2 and rig.state(r3) == L.REGISTERED
    assert "2 registry record(s)" in rig.last()["text"]
    assert rig.store.record(r2)["patient_id"] != rig.store.record(r1)["patient_id"]
    rig.store.close()


def test_two_candidates_are_offered_as_patient_1_and_patient_2(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111"), cover("AAA-112"), cover("AAA-113")]))
    to_validated(rig, 1); rig.press("Create new")
    to_validated(rig, 2); rig.press("None, create new")
    r3 = to_validated(rig, 3)
    assert [b["label"] for b in rig.last()["buttons"]] == ["Patient 1", "Patient 2", "None, create new", "I'm not sure"]
    rig.press("None, create new")
    assert len(rig.store.patients()) == 3 and rig.state(r3) == L.REGISTERED
    rig.store.close()


def test_i_am_not_sure_links_nothing_and_creates_nothing(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111"), cover("AAA-112")]))
    to_validated(rig, 1); rig.press("Create new")
    r2 = to_validated(rig, 2)
    rig.press("not sure")
    assert rig.state(r2) == L.MANUAL_REVIEW_REQUIRED
    assert rig.store.record(r2)["patient_id"] is None and len(rig.store.patients()) == 1
    assert all(o["record_id"] != r2 for o in rig.store.q("SELECT * FROM outbox"))
    rig.press("Decide now")                               # later, she decides
    assert rig.state(r2) == L.VALIDATED
    rig.press("Patient 1")
    assert rig.state(r2) == L.REGISTERED and len(rig.store.patients()) == 1
    rig.store.close()


def test_unreadable_code_asks_for_manual_entry_and_refuses_identifiers(tmp_path):
    raw = cover("x")
    raw["fields"]["fiche_number"] = [None, "ILLEGIBLE", 0.9]
    rig = Rig(tmp_path / "d", FakeExtractor(script=[raw]))
    rid = rig.capture(1); rig.tick()
    rig.press("Unreadable on paper")                      # follow-up on the illegible code
    rig.answer_all(); rig.press("Confirm")
    assert "type the code" in rig.last()["text"].lower()
    rig.say("06 00 76 13 48")
    assert "does not look like a registry code" in rig.last()["text"]
    rig.say("MW-7731")
    assert rig.store.record(rid)["linking_code"] == "MW-7731"
    rig.press("Create new")
    assert rig.store.patients()[0]["linking_code"] == "MW-7731"
    rig.store.close()


def test_patient_ids_do_not_derive_from_code(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111")]))
    to_validated(rig, 1); rig.press("Create new")
    pid = rig.store.patients()[0]["id"]
    # A random 128-bit id: a second patient with the SAME code gets a different id, so the id cannot be
    # derived from the code.  (The earlier check, "AAA" not in the id, failed by chance about once in
    # 140 runs, because a random hexadecimal id sometimes contains "AAA".)
    other = rig.store.create_patient(rig.mw["id"], "AAA-111", {})
    assert len(pid) == 32 and int(pid, 16) >= 0 and other != pid
    rig.store.close()
