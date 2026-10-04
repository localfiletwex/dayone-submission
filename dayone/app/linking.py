"""Patient linking.  This module only SUGGESTS candidates; it never links a
record to a patient and never creates a patient.  The midwife decides."""
from __future__ import annotations

from . import schema as S


def normalize_code(code) -> str:
    return "".join(c for c in str(code or "").upper() if c.isalnum())


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _known(record_data, section, key):
    v = record_data.get("sections", {}).get(section, {}).get("fields", {}).get(key)
    return v["value"] if v and v["status"] == S.KNOWN else None


def summary_from_record(record_data: dict) -> dict:
    """Non-identifying attributes used only to suggest matches."""
    return {"age": _known(record_data, "identification", "age"),
            "province": _known(record_data, "cover", "province"),
            "lmp_date": _known(record_data, "pregnancy", "lmp_date")}


def code_from_record(record_data: dict, tentative: bool = False):
    """The linking code.  `tentative=True` also returns a code the midwife has
    not confirmed yet: good enough to SUSPECT a duplicate, never to link."""
    v = record_data.get("sections", {}).get("cover", {}).get("fields", {}).get("fiche_number")
    if v and v["value"] is not None and (v["status"] == S.KNOWN or (tentative and v["status"] == S.NEEDS_REVIEW)):
        return v["value"]
    return None


def candidates(patients: list[dict], code, summary: dict, limit: int = 3) -> list[dict]:
    """Return plausible existing patients, best first, each with reasons."""
    ncode = normalize_code(code)
    out = []
    for p in patients:
        reasons, score, code_hit = [], 0.0, False
        pc = normalize_code(p.get("linking_code"))
        if ncode and pc:
            if ncode == pc:
                score, code_hit = score + 3, True
                reasons.append("same code")
            elif min(len(ncode), len(pc)) >= 4 and _edit_distance(ncode, pc) <= 2:
                score, code_hit = score + 2, True
                reasons.append("similar code")
        ps, demo = p.get("summary", {}), 0
        if summary.get("lmp_date") and summary["lmp_date"] == ps.get("lmp_date"):
            score, demo = score + 2, demo + 1
            reasons.append("same last menstrual period date")
        if isinstance(summary.get("age"), int) and isinstance(ps.get("age"), int) and abs(summary["age"] - ps["age"]) <= 1:
            score, demo = score + 1, demo + 1
            reasons.append("same age (within 1 year)")
        if summary.get("province") and ps.get("province") and \
                normalize_code(summary["province"]) == normalize_code(ps["province"]):
            score, demo = score + 0.5, demo + 1
            reasons.append("same province")
        if code_hit or demo >= 2:
            out.append({"patient": p, "score": score, "reasons": reasons, "exact_code": ncode == pc and bool(ncode)})
    out.sort(key=lambda c: -c["score"])
    return out[:limit]
