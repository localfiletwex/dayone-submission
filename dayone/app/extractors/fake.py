"""Deterministic stand-in for the vision model, for tests and for demoing the
conversation without an API key.

It does NOT read the image and does NOT use ground truth: it returns a small
set of INVENTED values (clearly not from the dataset), including one illegible
field, one low-confidence field and one implausible value, so that every
branch of the review flow can be exercised.  Never use it to report accuracy.
"""
from __future__ import annotations

import hashlib

from .base import ExtractionError, Extractor

_ORDER = ("cover", "identification", "pregnancy", "delivery")


def _k(v, c=0.97):
    return [v, "KNOWN", c]


_BLANK = [None, "NOT_PROVIDED", 0.95]

CANNED = {
    "cover": {"fields": {
        "fiche_number": _k("FAKE-000-001"), "region": _k("Région (factice)"), "province": _k("Province (factice)"),
        "facility_name": _k("CS Factice"), "facility_type": _k("CSU"), "coverage_mode": _k("Fixe"),
        "high_risk_pregnancy": _k(False), "risk_types": _k([])}, "groups": {}},
    "identification": {"fields": {
        "age": _k("29", 0.62),                                   # low confidence -> follow-up
        "education_level": [None, "ILLEGIBLE", 0.9],             # illegible -> follow-up
        "profession": _k("Sans, tél 06 12 34 56 78"),            # identifier inside free text -> scrubbed
        "consanguinity": _k(False), "desired_pregnancy": _k(True),
        "gravidity": _k("2"), "parity": _k("1"), "living_children": _k("1"),
        "history_medical": _k("RAS"), "history_surgical": [None, "STRUCK", 0.93],
        "tetanus_doses": _k(["1", "2"]), "rubella_vaccinated": _k(False), "hepatitis_b_vaccinated": _k(False),
    }, "groups": {"previous_deliveries": {"1": {
        "date": _k("14/03/2021"), "mode": _k("Voie basse"), "complication": _k("RAS"),
        "newborn_weight_g": _k("3300 g"), "newborn_complication": _k("RAS")}}}},
    "pregnancy": {"fields": {
        "lmp_date": _k("03/01/2026"), "height_cm": _k("162 cm"), "blood_group": _k("O"), "rhesus": _k("Rh+"),
        "expected_delivery_date": _k("10/10/2026"), "term_overrun_date": _BLANK,
    }, "groups": {"visits": {"t1_v1": {
        "attended_date": _k("01/03/2026"), "gestational_age_weeks": _k("8 SA"), "weight_kg": _k("61.5"),
        "blood_pressure": _k("118/76"), "hiv": _k("Neg"), "syphilis": _k("Neg"),
        "hemoglobin_g_dl": _k("112 g/dL", 0.9),                  # implausible -> follow-up
    }}}},
    "delivery": {"fields": {
        "place_setting": _k("En milieu surveillé"), "supervised_place": _k("Maternité"),
        "delivery_date": _k("08/10/2026"), "mode": _k("Voie basse non instrumentale"),
        "complications_present": _k(False), "newborn_status": _k("Vivant"), "newborn_sex": _k("F"),
        "birth_weight_g": _k("3210 g"), "head_circumference_cm": _k("34 cm", 0.7),
    }, "groups": {}},
}


class FakeExtractor(Extractor):
    name = "fake"

    def __init__(self, script=None, fail_times=0):
        """script: optional list of raw extractions (or exceptions) returned in order."""
        self.script = list(script or [])
        self.fail_times = fail_times
        self.calls = 0
        self.online = True

    def available(self) -> bool:
        return self.online

    def extract(self, image, mime="image/jpeg", page_type_hint=None):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ExtractionError("simulated model failure")
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        ptype = page_type_hint or _ORDER[int(hashlib.sha256(image).hexdigest(), 16) % len(_ORDER)]
        out = CANNED.get(ptype, {"fields": {}, "groups": {}})
        from .. import schema as S
        fields = {f.key: list(_BLANK) for f in S.SECTION_BY_KEY[ptype].fields}     # everything else: blank
        fields.update(out["fields"])
        return {"page_type": ptype, "fields": fields,
                "groups": {g: {s: dict(f) for s, f in items.items()} for g, items in out["groups"].items()}}
