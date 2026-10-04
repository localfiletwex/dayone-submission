from app import pipeline, schema as S
from app.normalize import canon, plausible, values_equal


def K(v, c=0.99):
    return [v, "KNOWN", c]


BLANK = [None, "NOT_PROVIDED", 0.99]


def test_status_names_match_the_specification():
    assert S.FIELD_STATUSES == ("KNOWN", "UNKNOWN", "NOT_PROVIDED", "ILLEGIBLE", "NOT_APPLICABLE", "NEEDS_REVIEW")


def test_every_field_always_has_an_explicit_status_never_na():
    for sec in S.SECTIONS:
        _, data, _ = pipeline.postprocess({"page_type": sec.key, "fields": {}, "groups": {}}, 0.85)
        for path, _, v in S.iter_fields(sec.key, data):
            assert v["status"] in S.FIELD_STATUSES
            assert v["value"] not in ("N/A", "NA", "n/a", "")
            # the model returned nothing => the agent must ask, not assume blank
            assert v["status"] == S.NEEDS_REVIEW, path


def test_model_writing_na_is_not_stored_as_a_value():
    for txt in ("—", "-", "/"):
        _, d, _ = pipeline.postprocess({"page_type": "delivery", "fields": {"abnormality": K(txt)}, "groups": {}}, 0.85)
        assert d["fields"]["abnormality"]["status"] == S.NOT_APPLICABLE and d["fields"]["abnormality"]["value"] is None
    _, d, _ = pipeline.postprocess({"page_type": "delivery", "fields": {"abnormality": K("?")}, "groups": {}}, 0.85)
    assert d["fields"]["abnormality"]["status"] == S.UNKNOWN


def test_blank_is_not_provided_unless_ruled_out_by_a_parent():
    raw = {"page_type": "delivery", "groups": {}, "fields": {
        "mode": K("Voie basse non instrumentale"), "cesarean_indication": BLANK, "instrumental_details": K([]),
        "complications_present": K(False), "complication_types": K([]), "complication_timing": K([]),
        "place_setting": K("En milieu surveillé"), "supervised_place": BLANK,
        "home_assisted_by_qualified_staff": K(False), "abnormality": BLANK}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    f = d["fields"]
    assert f["cesarean_indication"]["status"] == S.NOT_APPLICABLE      # not a cesarean
    assert f["instrumental_details"]["status"] == S.NOT_APPLICABLE
    assert f["home_assisted_by_qualified_staff"]["status"] == S.NOT_APPLICABLE   # not a home birth
    # the complications block has no tick at all: every box in it is NOT_PROVIDED
    for k in ("complications_present", "complication_types", "complication_timing"):
        assert f[k]["status"] == S.NOT_PROVIDED and f[k]["value"] is None
    assert f["supervised_place"]["status"] == S.NOT_PROVIDED           # applicable but left blank
    assert f["abnormality"]["status"] == S.NOT_PROVIDED


def test_dependency_never_overrides_a_written_value():
    raw = {"page_type": "delivery", "groups": {}, "fields": {
        "mode": K("Voie basse non instrumentale"), "cesarean_indication": K("Utérus cicatriciel")}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    assert d["fields"]["cesarean_indication"]["status"] == S.KNOWN


def test_dependency_needs_a_known_parent():
    raw = {"page_type": "delivery", "groups": {}, "fields": {"mode": BLANK, "cesarean_indication": BLANK}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    assert d["fields"]["cesarean_indication"]["status"] == S.NOT_PROVIDED


def test_no_visit_is_created_for_a_fully_empty_column():
    empty = {f.key: BLANK for f in S.PREGNANCY.groups[0].fields}
    filled = dict(empty, weight_kg=K("61.5"), fetal_movements=[None, "STRUCK", 0.95])
    raw = {"page_type": "pregnancy", "fields": {"rhesus": K("Rh+")},
           "groups": {"visits": {"t1_v1": empty, "t1_v2": filled, "m9": empty}}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    visits = d["groups"]["visits"]
    assert [v["slot"] for v in visits] == ["t1_v2"]
    f = visits[0]["fields"]
    assert f["weight_kg"]["value"] == 61.5
    assert f["fetal_movements"]["status"] == S.NOT_APPLICABLE       # struck by the midwife
    assert f["blood_pressure"]["status"] == S.NOT_PROVIDED          # blank cell in a real visit
    assert f["rai"]["status"] == S.NOT_APPLICABLE                   # Rh+ => RAI not applicable


def test_visits_are_a_repeating_list_in_slot_order():
    one = {"weight_kg": K("60")}
    raw = {"page_type": "pregnancy", "fields": {}, "groups": {"visits": {"m8": one, "t1_v1": one, "bogus": one}}}
    _, d, info = pipeline.postprocess(raw, 0.85)
    assert [v["slot"] for v in d["groups"]["visits"]] == ["t1_v1", "m8"]
    assert "visits[bogus]" in info["dropped_keys"]


def test_low_confidence_and_implausible_values_go_to_review_without_being_changed():
    raw = {"page_type": "identification", "groups": {}, "fields": {
        "age": K("31", 0.5), "gravidity": K("3"), "parity": K("1"), "living_children": K("112"),
        "rubella_vaccine_date": K("31/02/2022")}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    f = d["fields"]
    assert (f["age"]["status"], f["age"]["value"]) == (S.NEEDS_REVIEW, 31)
    assert (f["living_children"]["status"], f["living_children"]["value"]) == (S.NEEDS_REVIEW, 112)
    assert (f["rubella_vaccine_date"]["status"], f["rubella_vaccine_date"]["value"]) == (S.NEEDS_REVIEW, "31/02/2022")
    assert f["gravidity"]["status"] == S.KNOWN


def test_uncertain_blank_is_asked_not_assumed():
    raw = {"page_type": "delivery", "groups": {}, "fields": {"abnormality": [None, "NOT_PROVIDED", 0.4]}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    assert d["fields"]["abnormality"]["status"] == S.NEEDS_REVIEW


def test_illegible_stays_illegible_and_is_never_guessed():
    raw = {"page_type": "delivery", "groups": {}, "fields": {"abnormality": ["Aucun?", "ILLEGIBLE", 0.9]}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    assert d["fields"]["abnormality"] ["status"] == S.ILLEGIBLE and d["fields"]["abnormality"]["value"] is None


def test_cross_checks_only_flag():
    raw = {"page_type": "identification", "groups": {}, "fields": {"gravidity": K("1"), "parity": K("3")}}
    _, d, _ = pipeline.postprocess(raw, 0.85)
    assert d["fields"]["gravidity"]["status"] == d["fields"]["parity"]["status"] == S.NEEDS_REVIEW
    assert d["fields"]["parity"]["value"] == 3


def test_merge_never_overwrites_midwife_values():
    old = {"fields": {"age": S.fv(30, S.KNOWN, 1.0, "midwife"), "parity": S.fv(None, S.NEEDS_REVIEW, None, "none")},
           "groups": {}}
    new = {"fields": {"age": S.fv(39, S.KNOWN, 0.99, "ai"), "parity": S.fv(2, S.KNOWN, 0.99, "ai")}, "groups": {}}
    m = pipeline.merge_section(old, new)
    assert m["fields"]["age"]["value"] == 30 and m["fields"]["parity"]["value"] == 2


def test_canon_and_equality():
    age, dt, w = S.IDENTIFICATION.field("age"), S.PREGNANCY.field("lmp_date"), S.DELIVERY.field("birth_weight_g")
    assert canon(dt, "26/04/2025") == ("2025-04-26", True)
    assert canon(dt, "2023") == ("2023", False)
    assert canon(w, "3626 g") == (3626, True) and canon(age, "trente") == ("trente", False)
    assert canon(S.PP_EARLY_MOTHER.field("blood_pressure"), "110 / 71") == ("110/71", True)
    assert not plausible(age, 112) and plausible(age, 31)
    txt = S.DELIVERY.field("cesarean_indication")
    assert values_equal(txt, "Ut�rus cicatriciel", "Utérus cicatriciel")
    assert values_equal(txt, "Ut�rus cicatriciel", "Ut rus cicatriciel")
    assert not values_equal(txt, "Utérus cicatriciel", "Utérus cicatrisé")
    assert values_equal(S.COVER.field("risk_types"), ["Anémie", "H.T.A"], ["H.T.A", "Anémie"])


def test_fv_guards():
    import pytest
    with pytest.raises(ValueError):
        S.fv("x", "N/A")
    with pytest.raises(ValueError):
        S.fv(None, S.KNOWN)
    assert S.fv("x", S.ILLEGIBLE)["value"] is None


def test_checkbox_group_rule_and_ticked_wording():
    def run(cons, desired):
        raw = {"page_type": "identification", "groups": {}, "fields": {"consanguinity": K(cons), "desired_pregnancy": K(desired)}}
        f = pipeline.postprocess(raw, 0.85)[1]["fields"]
        return f["consanguinity"], f["desired_pregnancy"]
    a, b = run(False, True)                       # the group was used: unticked means "not ticked"
    assert (a["status"], a["value"]) == (S.KNOWN, "not ticked") and (b["status"], b["value"]) == (S.KNOWN, "ticked")
    a, b = run(False, False)                      # no tick in the group: nothing is known
    assert a["status"] == b["status"] == S.NOT_PROVIDED and a["value"] is None
    for v in ("No", "Non", "no", False, "Oui", True):
        assert canon(S.IDENTIFICATION.field("consanguinity"), v)[0] in ("ticked", "not ticked")
    from app.normalize import display
    assert display(S.IDENTIFICATION.field("consanguinity"), "not ticked") == "not ticked"


def test_group_rule_is_reversible_when_a_sibling_is_ticked_later():
    data = {"fields": {"consanguinity": S.fv("not ticked", S.KNOWN, 1.0, "midwife"),
                       "desired_pregnancy": S.fv("not ticked", S.KNOWN, 1.0, "midwife")}, "groups": {}}
    S.apply_blank_rules("identification", data)
    assert data["fields"]["consanguinity"]["status"] == S.NOT_PROVIDED
    data["fields"]["desired_pregnancy"] = S.fv("ticked", S.KNOWN, 1.0, "midwife")
    S.apply_blank_rules("identification", data)
    assert (data["fields"]["consanguinity"]["status"], data["fields"]["consanguinity"]["value"]) == (S.KNOWN, "not ticked")


def test_multi_select_with_no_tick_follows_its_group():
    raw = {"page_type": "pp_early_newborn", "groups": {}, "fields": {
        "danger_signs": K([]), "vaccines_today": K([]), "vitamin_d": K(True)}}
    f = pipeline.postprocess(raw, 0.85)[1]["fields"]
    assert f["danger_signs"]["status"] == S.NOT_PROVIDED                       # its own group, nothing ticked
    assert (f["vaccines_today"]["status"], f["vaccines_today"]["value"]) == (S.KNOWN, [])   # vitamin D ticked beside it


def test_every_checkbox_field_belongs_to_exactly_one_group():
    for sec in S.SECTIONS:
        seen = [f.key for fs in S.checkbox_groups(sec.key).values() for f in fs]
        assert sorted(seen) == sorted(f.key for f in sec.fields if f.kind in (S.BOOL, S.CHOICE, S.MULTI))


def test_linking_code_and_infection_results_are_always_flagged():
    raw = {"page_type": "cover", "groups": {}, "fields": {"fiche_number": K("2026-1", 1.0), "region": K("R", 1.0)}}
    f = pipeline.postprocess(raw, 0.5)[1]["fields"]
    assert (f["fiche_number"]["status"], f["fiche_number"]["value"]) == (S.NEEDS_REVIEW, "2026-1")
    assert f["region"]["status"] == S.KNOWN
    row = {"hiv": K("Neg", 1.0), "syphilis": K("Neg", 1.0), "hbs_antigen": K("Neg", 1.0), "rubella": K("Immune", 1.0),
           "glycosuria": [None, "NOT_PROVIDED", 1.0]}
    raw = {"page_type": "pregnancy", "fields": {}, "groups": {"visits": {"t1_v1": row}}}
    v = pipeline.postprocess(raw, 0.5)[1]["groups"]["visits"][0]["fields"]
    assert [v[k]["status"] for k in ("hiv", "syphilis", "hbs_antigen")] == [S.NEEDS_REVIEW] * 3
    assert v["hiv"]["value"] == "Neg" and v["rubella"]["status"] == S.KNOWN
    assert {f.key for f in S.PREGNANCY.groups[0].fields if f.always_confirm} == {"hiv", "syphilis", "hbs_antigen"}
    assert "hepatitis_c" not in {f.key for f in S.PREGNANCY.groups[0].fields}
