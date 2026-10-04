"""Anonymised aggregates of the synchronised records (bonus dashboard).

Illustrates the epidemiological use of the data on the simulated central
server: how many registries, and how blood pressure, temperature and the
infection test results recorded on the form are distributed.

What it deliberately does NOT do:
* it shows no individual record, no code, no date and no facility;
* any group smaller than `MIN_CELL` is shown as "<MIN_CELL" (small-cell
  suppression), so that one woman cannot be singled out; when only ONE group
  of a table is that small, a second group is hidden as well, otherwise the
  small one could be worked out by subtracting the others from the total;
* it interprets nothing: the bins are plain numeric ranges, there is no
  risk colour, no threshold and no advice (triage and clinical decision
  support are out of scope).

Only fields with status KNOWN count: a value still waiting for the midwife's
review never reaches the server, and blank / illegible cells are not values.

The form has no hepatitis C field; it records the hepatitis B surface antigen
(Ag HBs), which is what is counted here, under its real name.
"""
from __future__ import annotations

import os

from . import schema as S
from .normalize import fold

MIN_CELL = int(os.environ.get("DAYONE_DASHBOARD_MIN_CELL", "5"))

SYSTOLIC_BINS = ((None, 100), (100, 120), (120, 140), (140, 160), (160, None))        # mmHg
DIASTOLIC_BINS = ((None, 60), (60, 80), (80, 90), (90, 100), (100, None))             # mmHg
TEMPERATURE_BINS = ((None, 36.0), (36.0, 37.5), (37.5, 38.0), (38.0, None))           # °C
TESTS = ("hiv", "syphilis", "hbs_antigen")                                            # rows of the antenatal visits table
TEST_CATEGORIES = ("negative", "positive", "other", "none")


def latest_records(payloads: list[dict]) -> list[dict]:
    """One payload per registry: the highest version, and not a registry that a
    later one replaced (re-digitisation)."""
    best: dict[str, dict] = {}
    for p in payloads:
        if p["record_id"] not in best or p["version"] > best[p["record_id"]]["version"]:
            best[p["record_id"]] = p
    replaced = {p.get("supersedes") for p in best.values() if p.get("supersedes")}
    return [p for rid, p in best.items() if rid not in replaced]


def _known(section: dict | None, key: str):
    v = (section or {}).get("fields", {}).get(key)
    return v["value"] if v and v.get("status") == S.KNOWN else None


def _visits(payload: dict) -> list[dict]:
    return (payload.get("sections", {}).get("pregnancy") or {}).get("groups", {}).get("visits", [])


def _label(lo, hi) -> str:
    if lo is None:
        return f"< {hi:g}"
    if hi is None:
        return f"≥ {lo:g}"
    return f"{lo:g} – {hi:g}"          # lower bound included, upper bound excluded


def _histogram(values, bins) -> list[dict]:
    return [{"label": _label(lo, hi),
             "n": sum(1 for v in values if (lo is None or v >= lo) and (hi is None or v < hi))} for lo, hi in bins]


def test_category(text) -> str:
    """'Neg' / 'négatif' / 'سلبي' -> negative; 'Pos' / '+' -> positive; anything else -> other.
    Only sorts what is written into a count; it never becomes part of a record."""
    raw = str(text).strip()
    f = fold(raw)
    if raw in ("-", "(-)") or f.startswith(("neg", "nonreact", "سلب")) or f == "nr":
        return "negative"
    if raw in ("+", "(+)") or f.startswith(("pos", "react", "ايجاب", "موجب")):
        return "positive"
    return "other"


def _cell(n: int, min_cell: int) -> dict:
    """A count as published: exact, or hidden when it is a small non-zero group."""
    if 0 < n < min_cell:
        return {"n": None, "shown": f"< {min_cell}", "suppressed": True}
    return {"n": n, "shown": str(n), "suppressed": False}


def _protect(cells: list[dict]) -> list[dict]:
    """Secondary suppression.  If exactly one cell of a table is hidden, its value
    is total minus the others; so hide the smallest other non-empty cell too."""
    if sum(c["suppressed"] for c in cells) == 1:
        others = [c for c in cells if not c["suppressed"] and c["n"]]
        if others:
            smallest = min(others, key=lambda c: c["n"])
            smallest.update(n=None, shown="hidden", suppressed=True, secondary=True)
    return cells


def aggregate(payloads: list[dict], min_cell: int | None = None) -> dict:
    min_cell = MIN_CELL if min_cell is None else max(1, int(min_cell))
    records = latest_records(payloads)
    systolic, diastolic, temp_mother, temp_newborn = [], [], [], []
    tests = {t: dict.fromkeys(TEST_CATEGORIES, 0) for t in TESTS}
    for p in records:
        sections = p.get("sections", {})
        pressures = [it["fields"].get("blood_pressure") for it in _visits(p)]
        pressures = [v["value"] for v in pressures if v and v.get("status") == S.KNOWN]
        for key in ("pp_early_mother", "pp_late_mother"):
            pressures.append(_known(sections.get(key), "blood_pressure"))
            t = _known(sections.get(key), "temperature_c")
            if isinstance(t, (int, float)):
                temp_mother.append(float(t))
        for key in ("pp_early_newborn", "pp_late_newborn"):
            t = _known(sections.get(key), "temperature_c")
            if isinstance(t, (int, float)):
                temp_newborn.append(float(t))
        for bp in pressures:
            try:
                s, d = (int(x) for x in str(bp).split("/"))
            except (TypeError, ValueError):
                continue
            systolic.append(s)
            diastolic.append(d)
        for t in TESTS:                      # one category per registry: what the visits table says overall
            seen = {test_category(it["fields"][t]["value"]) for it in _visits(p)
                    if it["fields"].get(t, {}).get("status") == S.KNOWN}
            tests[t]["positive" if "positive" in seen else "negative" if "negative" in seen
                     else "other" if seen else "none"] += 1

    def chart(cid, values, bins, unit):
        return {"id": cid, "unit": unit, "total": _cell(len(values), min_cell),
                "bins": _protect([{"label": b["label"], **_cell(b["n"], min_cell)} for b in _histogram(values, bins)])}

    return {
        "min_cell": min_cell,
        "registries": _cell(len(records), min_cell),
        "patients": _cell(len({p.get("patient_id") for p in records if p.get("patient_id")}), min_cell),
        "charts": [chart("bp_systolic", systolic, SYSTOLIC_BINS, "mmHg"),
                   chart("bp_diastolic", diastolic, DIASTOLIC_BINS, "mmHg"),
                   chart("temp_mother", temp_mother, TEMPERATURE_BINS, "°C"),
                   chart("temp_newborn", temp_newborn, TEMPERATURE_BINS, "°C")],
        "tests": [{"id": t, "categories": _protect([{"id": c, **_cell(tests[t][c], min_cell)} for c in TEST_CATEGORIES])}
                  for t in TESTS],
    }
