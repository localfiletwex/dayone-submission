import uuid

import pytest

from app import pipeline, schema as S
from app.redaction import contains_identifier, scrub
from app.storage import AccessDenied, Store, WrongKey
from tests.conftest import Rig, img

IDENTIFIERS = ["Tazi Meryem", "CB609814", "06 00 76 13 48", "0600761348", "+212 6 00 76 13 48",
               "49 Rue Al Qods, Kénitra", "Dr Benjelloun", "Sage-femme Hajar", "Inf. Zahra"]


@pytest.mark.parametrize("text,kind", [
    ("CIN : CB609814", "national_id"), ("tél 06 00 76 13 48", "phone"), ("+212 6 00 76 13 48", "phone"),
    ("0600761348", "phone"), ("49 Rue Al Qods, Kénitra", "address"), ("Hay Salam bloc 3", "address"),
    ("Dr Benjelloun", "person_name"), ("Sage-femme Hajar", "person_name"), ("Inf. Zahra", "person_name"),
    ("vu par Dr El Idrissi", "person_name")])
def test_scrub_removes_identifiers(text, kind):
    clean, found = scrub(text)
    assert kind in found and "[REDACTED]" in clean
    for secret in ("CB609814", "76 13 48", "0600761348", "Al Qods", "Benjelloun", "Hajar", "Zahra", "Idrissi", "Salam"):
        assert secret not in clean


@pytest.mark.parametrize("text", ["RAS", "Néant", "3626 g", "104/74", "12/05/2022", "11.8 g/dL", "Voie basse",
                                  "2026-823-001", "Poursuivre l'allaitement exclusif", "Utérus cicatriciel",
                                  "Sage-femme", "0.93 g/L", "38 SA", "Cycles réguliers", "Fer 2 cp/j"])
def test_scrub_leaves_clinical_text_alone(text):
    assert scrub(text) == (text, [])


def test_schema_has_no_identifier_field():
    banned = ("name", "nom", "cin", "phone", "telephone", "address", "adresse", "husband_name", "examiner", "seen_by")
    for sec in S.SECTIONS:
        keys = [f.key for f in sec.fields] + [f.key for g in sec.groups for f in g.fields]
        for k in keys:
            assert k == "facility_name" or not any(b == k or k.startswith(b + "_") or k.endswith("_" + b) for b in banned), k


def test_pipeline_drops_keys_outside_the_schema_and_scrubs_values():
    raw = {"page_type": "identification", "fields": {
        "patient_name": ["Tazi Meryem", "KNOWN", 0.99], "cin": ["CB609814", "KNOWN", 0.99],
        "husband_name": ["Mohamed Tazi", "KNOWN", 0.99], "phone": ["06 00 76 13 48", "KNOWN", 0.99],
        "address": ["49 Rue Al Qods", "KNOWN", 0.99],
        "age": ["31", "KNOWN", 0.99], "profession": ["Étudiante, tél 06 00 76 13 48", "KNOWN", 0.99],
        "history_medical": ["suivie par Dr Benjelloun", "KNOWN", 0.99]}, "groups": {}}
    ptype, data, info = pipeline.postprocess(raw, 0.85)
    blob = repr(data)
    for secret in ("Tazi", "CB609814", "Mohamed", "76 13 48", "Al Qods", "Benjelloun"):
        assert secret not in blob
    assert set(info["dropped_keys"]) == {"patient_name", "cin", "husband_name", "phone", "address"}
    assert data["fields"]["age"]["value"] == 31
    # a scrubbed value is never silently accepted
    assert data["fields"]["profession"]["status"] == S.NEEDS_REVIEW


def test_wrong_pin_cannot_open_the_database(rig):
    rig.photo(1)
    with pytest.raises(WrongKey):
        Store(rig.dir, "0000")


def test_nothing_readable_on_disk(rig):
    marker = b"PLAINTEXT-MARKER-12345"
    rid = rig.store.create_record(rig.mw["id"])
    rig.store.add_page(rid, rig.mw["id"], marker * 10)
    rig.store.add_message(rig.mw["id"], "bot", "PLAINTEXT-MARKER-12345 midwife1 CAPTURED")
    rig.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for f in rig.dir.rglob("*"):
        if f.is_file():
            content = f.read_bytes()
            assert marker not in content, f
            assert b"SQLite format 3" not in content, f
            assert b"midwife1" not in content and b"CAPTURED" not in content, f


def test_image_roundtrip_and_role_based_access(rig):
    rid = rig.store.create_record(rig.mw["id"])
    pid, _ = rig.store.add_page(rid, rig.mw["id"], img(7))
    assert rig.store.read_image(pid, rig.mw)[0] == img(7)
    assert rig.store.read_image(pid, rig.store.user("supervisor1"))[0] == img(7)
    for who in ("data1", "midwife2"):
        with pytest.raises(AccessDenied):
            rig.store.read_image(pid, rig.store.user(who))
    kinds = [e["kind"] for e in rig.store.events(rid)]
    assert kinds.count("image_viewed") == 2 and kinds.count("image_access_denied") == 2


def test_original_image_is_linked_to_record_date_midwife_and_status(rig):
    rig.photo(1)
    p = rig.store.pages(rig.rid())[0]
    assert p["record_id"] == rig.rid() and p["midwife_id"] == rig.mw["id"]
    assert p["captured_at"] and p["status"] == "CAPTURED" and len(p["sha256"]) == 64


def test_internal_ids_are_random_not_derived(rig):
    rig.capture(1); rig.tick(); rig.register()
    rec = rig.store.record(rig.rid())
    for ident in (rec["id"], rec["patient_id"], rig.store.pages(rec["id"])[0]["id"]):
        assert uuid.UUID(hex=ident).version == 4
        assert "FAKE" not in ident.upper() and "000001" not in ident
    # two patients created from identical data still get different ids
    a = rig.store.create_patient(rig.mw["id"], "X-1", {"age": 30})
    b = rig.store.create_patient(rig.mw["id"], "X-1", {"age": 30})
    assert a != b


def test_no_identifier_reaches_storage_or_sync_after_a_full_flow(tmp_path, caplog):
    """The model misbehaves and returns identifiers everywhere it can."""
    from app.extractors.fake import FakeExtractor
    raw = {"page_type": "identification", "fields": {
        "nom": ["Tazi Meryem", "KNOWN", 0.99], "cin": ["CB609814", "KNOWN", 0.99],
        "age": ["31", "KNOWN", 0.99], "profession": ["Étudiante 06 00 76 13 48", "KNOWN", 0.99],
        "education_level": ["CB609814", "KNOWN", 0.99],
        "history_medical": ["49 Rue Al Qods, Kénitra", "KNOWN", 0.99],
        "history_surgical": ["opérée par Dr Benjelloun", "KNOWN", 0.99]},
        "groups": {"previous_deliveries": {"1": {"complication": ["Inf. Zahra", "KNOWN", 0.99],
                                                 "husband": ["Mohamed Tazi", "KNOWN", 0.9]}}}}
    rig = Rig(tmp_path / "d", FakeExtractor(script=[raw]))
    with caplog.at_level("DEBUG"):
        rig.photo(1); rig.say("cmd:done"); rig.tick()
        rig.say("Mon numéro est 06 00 76 13 48")          # the midwife types an identifier in the chat
        rig.register(); rig.tick()
    assert rig.state(rig.rid()) == "SYNCED"
    dump = "\n".join(repr(r) for t in ("records", "pages", "events", "messages", "patients", "sessions", "outbox")
                     for r in rig.store.q(f"SELECT * FROM {t}"))
    dump += repr(list(rig.server.received.values())) + caplog.text
    for secret in ("Tazi", "Meryem", "CB609814", "76 13 48", "Al Qods", "Benjelloun", "Zahra", "Mohamed"):
        assert secret not in dump, secret
    assert not any("image" in k for p in rig.server.received.values() for pg in p["pages"] for k in pg)
    rig.store.close()


def test_log_filter_scrubs(caplog):
    from app.redaction import get_logger
    with caplog.at_level("INFO"):
        get_logger("dayone.test").info("value %s for %s", "CB609814", "06 00 76 13 48")
    assert "CB609814" not in caplog.text and "76 13 48" not in caplog.text


def test_contains_identifier():
    assert contains_identifier("CIN BK941500") and not contains_identifier("RAS")


def test_a_facility_named_after_its_neighbourhood_is_not_an_address(tmp_path):
    """Seen on the dataset: "CSC Hay Salam" was masked as an address and could not be typed back."""
    assert scrub("CSC Hay Salam")[1] == ["address"]                          # in any other field it still is
    assert scrub("CSC Hay Salam", S.SCRUB_ALLOW["facility_name"]) == ("CSC Hay Salam", [])
    assert scrub("CSC Hay Salam tel 0600761348", ("address",))[1] == ["phone"]   # other identifiers still go
    raw = {"page_type": "cover", "groups": {}, "fields": {
        "facility_name": ["CSC Hay Salam", "KNOWN", 0.99], "province": ["Hay Salam", "KNOWN", 0.99]}}
    _, section, info = pipeline.postprocess(raw, 0.5)
    assert section["fields"]["facility_name"] | {"note": ""} == S.fv("CSC Hay Salam", S.KNOWN, 0.99, "ai")
    assert section["fields"]["province"]["status"] == S.NEEDS_REVIEW and "address" in info["redactions"]
    from app.extractors.fake import FakeExtractor
    rig = Rig(tmp_path / "d", FakeExtractor(script=[{"page_type": "cover", "groups": {}, "fields": {
        "facility_name": [None, "ILLEGIBLE", 0.9]}}]))
    rid = rig.capture(1); rig.tick()
    while "Health facility" not in rig.prompt()["text"]:
        rig.say("ans:blank")
    rig.say("CSUA Hay Riad")                                                  # the midwife types it
    assert rig.store.record(rid)["data"]["sections"]["cover"]["fields"]["facility_name"]["value"] == "CSUA Hay Riad"
    rig.store.close()
