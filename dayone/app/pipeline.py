"""Turn a raw extraction into schema-typed field values with a final status
and confidence, with identifiers removed BEFORE anything is stored.

raw (from an Extractor)  --sanitize_raw-->  only schema keys, text scrubbed
                         --postprocess-->   {"fields": {...}, "groups": {...}}
"""
from __future__ import annotations

from datetime import date

from . import schema as S
from .normalize import canon, fold, plausible
from . import languages
from .redaction import scrub

_UNKNOWN_FOLDED = {fold(x) for x in S.UNKNOWN_MARKS if fold(x)}


def _triple(x):
    """Accept [v, s, c] or {"value","status","confidence"}; anything else is malformed."""
    if isinstance(x, dict):
        x = [x.get("value", x.get("v")), x.get("status", x.get("s")), x.get("confidence", x.get("c"))]
    if not isinstance(x, (list, tuple)) or len(x) != 3:
        return None
    v, s, c = x
    try:
        c = min(1.0, max(0.0, float(c)))
    except (TypeError, ValueError):
        c = 0.0
    return [v, s if isinstance(s, str) else "", c]


def _in_arabic(v) -> bool:
    return any(languages.has_arabic(i) for i in v) if isinstance(v, list) else languages.has_arabic(v)


def _scrub_value(v, redactions, allow=()):
    if isinstance(v, str):
        clean, found = scrub(v, allow)
        if found:
            redactions.extend(found)
        return clean
    if isinstance(v, list):
        return [_scrub_value(i, redactions, allow) for i in v]
    return v


def sanitize_raw(raw: dict) -> dict:
    """Drop every key that is not in the schema and scrub identifiers from
    every string.  The result is safe to cache or store."""
    ptype = raw.get("page_type")
    if ptype not in S.SECTION_BY_KEY:
        return {"page_type": None, "fields": {}, "groups": {}, "dropped": ["*"], "redactions": []}
    sec = S.SECTION_BY_KEY[ptype]
    dropped, redactions = [], []
    # Paths of the values written in Arabic script.  Noted HERE, because scrubbing turns Arabic-script
    # digits into Western ones and the script could not be told afterwards.  A page with at least one
    # such value is an "Arabic page" for the optional stricter threshold (see finalize).
    arabic = list(raw.get("arabic", []))
    known = {f.key for f in sec.fields}
    fields = {}
    for k, x in (raw.get("fields") or {}).items():
        if k not in known:
            dropped.append(k)
            continue
        t = _triple(x)
        if t:
            if _in_arabic(t[0]):
                arabic.append(k)
            t[0] = _scrub_value(t[0], redactions, S.SCRUB_ALLOW.get(k, ()))
        fields[k] = t
    groups = {}
    for g in sec.groups:
        gk = {f.key for f in g.fields}
        slots = {s[0] for s in g.slots}
        items = (raw.get("groups") or {}).get(g.name) or {}
        if isinstance(items, list):          # tolerate [{slot:..., ...}]
            items = {str(i.get("slot", i.get("id", ""))): {k: v for k, v in i.items() if k not in ("slot", "id")}
                     for i in items if isinstance(i, dict)}
        out = {}
        for slot, cells in items.items():
            if str(slot) not in slots or not isinstance(cells, dict):
                dropped.append(f"{g.name}[{slot}]")
                continue
            row = {}
            for k, x in cells.items():
                if k not in gk:
                    dropped.append(f"{g.name}.{k}")
                    continue
                t = _triple(x)
                if t:
                    if _in_arabic(t[0]):
                        arabic.append(f"{g.name}[{slot}].{k}")
                    t[0] = _scrub_value(t[0], redactions)
                row[k] = t
            out[str(slot)] = row
        groups[g.name] = out
    for g in (raw.get("groups") or {}):
        if g not in {x.name for x in sec.groups}:
            dropped.append(g)
    return {"page_type": ptype, "fields": fields, "groups": groups, "arabic": sorted(set(arabic)),
            "dropped": dropped, "redactions": sorted(set(redactions + list(raw.get("redactions", []))))}


def finalize(f: S.Field, triple, threshold: float, missing_status=S.NEEDS_REVIEW, threshold_arabic=None,
             arabic=False, arabic_scope="numbers") -> dict:
    """One raw [value, status, confidence] -> final field value.

    `threshold_arabic`, when given, replaces `threshold` on a page that has
    entries in Arabic script (`arabic`), for its numbers (`arabic_scope`
    "numbers": integers, decimals, dates, blood pressures) or for every filled
    field ("all").  See config.load_threshold.

    Why the PAGE and not the value: what was measured on Arabic pages is misread
    Arabic-script digits (2 and 3 above all), and the model returns those as
    plain numbers, so a value no longer shows which script it was written in."""
    if triple is None:
        note = "the AI did not return this field" if missing_status == S.NEEDS_REVIEW else "not returned by the AI"
        return S.fv(None, missing_status, None if missing_status != S.NEEDS_REVIEW else 0.0, "ai", note)
    v, s, c = triple
    if s == "KNOWN" and isinstance(v, str):
        if v.strip() in S.STRIKE_MARKS:
            s, v = "STRUCK", None
        elif v.strip().lower() in S.UNKNOWN_MARKS or fold(v) in _UNKNOWN_FOLDED:
            s, v = "UNKNOWN", None
    if s == "KNOWN" and (v is None or (isinstance(v, str) and not v.strip())):
        s = "NOT_PROVIDED"
    if s == "KNOWN":
        value, ok = canon(f, v)
        notes = []
        if isinstance(v, str) and "[REDACTED]" in v:
            notes.append("a possible personal identifier was removed")
        if not ok:
            notes.append("I could not interpret this as a " + f.kind)
        elif not plausible(f, value):
            notes.append("the value is outside the expected range")
        strict = (threshold_arabic is not None and arabic
                  and (arabic_scope == "all" or f.kind in S.NUMERIC_KINDS))
        if c < (threshold_arabic if strict else threshold):
            notes.append("low confidence")
        if value is None or value == "":
            return S.fv(None, S.NEEDS_REVIEW, c, "ai", "empty value")
        if f.always_confirm:
            notes.append("always confirmed with you (linking code or infection test result)")
        return S.fv(value, S.NEEDS_REVIEW if notes else S.KNOWN, c, "ai", "; ".join(notes))
    if s == "ILLEGIBLE":
        return S.fv(None, S.ILLEGIBLE, c, "ai", "written but I cannot read it")
    final = {"NOT_PROVIDED": S.NOT_PROVIDED, "STRUCK": S.NOT_APPLICABLE, "UNKNOWN": S.UNKNOWN}.get(s)
    if final is None:
        return S.fv(None, S.NEEDS_REVIEW, 0.0, "ai", "the AI returned an invalid status")
    if c < threshold:
        what = {S.NOT_PROVIDED: "blank", S.NOT_APPLICABLE: "struck through", S.UNKNOWN: "marked unknown"}[final]
        return S.fv(None, S.NEEDS_REVIEW, c, "ai", f"looks {what}, but I am not sure")
    return S.fv(None, final, c, "ai")


def _days(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def cross_checks(ptype: str, data: dict) -> None:
    """Internal-consistency checks.  They only ever LOWER trust (KNOWN ->
    NEEDS_REVIEW); they never change a value and are not clinical advice."""
    f = data["fields"]

    def known(k):
        return k in f and f[k]["status"] == S.KNOWN and f[k]["source"] == "ai"

    def flag(k, why):
        f[k] = S.fv(f[k]["value"], S.NEEDS_REVIEW, f[k]["confidence"], "ai", why)

    if ptype == "identification":
        if known("gravidity") and known("parity") and f["parity"]["value"] > f["gravidity"]["value"]:
            flag("gravidity", "parity is greater than gravidity - please check both")
            flag("parity", "parity is greater than gravidity - please check both")
    if ptype == "pregnancy" and known("lmp_date") and known("expected_delivery_date"):
        d = _days(f["lmp_date"]["value"], f["expected_delivery_date"]["value"])
        if d is not None and not 259 <= d <= 301:
            flag("lmp_date", "the two dates are not about 40 weeks apart - please check both")
            flag("expected_delivery_date", "the two dates are not about 40 weeks apart - please check both")


def postprocess(raw: dict, threshold: float, threshold_arabic: float | None = None,
                arabic_scope: str = "numbers") -> tuple[str, dict, dict]:
    """Return (page_type, section_data, info)."""
    clean = sanitize_raw(raw)
    ptype = clean["page_type"]
    if ptype is None:
        raise ValueError("unknown page type")
    sec = S.SECTION_BY_KEY[ptype]
    ar, arabic_page = threshold_arabic, bool(clean["arabic"])      # at least one entry in Arabic script
    data = {"fields": {f.key: finalize(f, clean["fields"].get(f.key), threshold, threshold_arabic=ar,
                                       arabic=arabic_page, arabic_scope=arabic_scope) for f in sec.fields},
            "groups": {}}
    for g in sec.groups:
        order = [s[0] for s in g.slots]
        items = []
        for slot in sorted(clean["groups"].get(g.name, {}), key=order.index):
            cells = clean["groups"][g.name][slot]
            items.append({"slot": slot,
                          "fields": {f.key: finalize(f, cells.get(f.key), threshold, S.NOT_PROVIDED, ar, arabic_page,
                                                     arabic_scope) for f in g.fields}})
        data["groups"][g.name] = items
    S.apply_blank_rules(ptype, data)
    cross_checks(ptype, data)
    return ptype, data, {"dropped_keys": clean["dropped"], "redactions": clean["redactions"]}


def merge_section(existing: dict | None, new: dict) -> dict:
    """Merge a fresh AI section into an existing one WITHOUT overwriting
    anything the midwife entered or confirmed."""
    if not existing:
        return new
    out = {"fields": dict(new["fields"]), "groups": {g: list(v) for g, v in new.get("groups", {}).items()}}
    for k, v in existing.get("fields", {}).items():
        if v.get("source") == "midwife":
            out["fields"][k] = v
    for g, items in existing.get("groups", {}).items():
        by_slot = {it["slot"]: it for it in out["groups"].setdefault(g, [])}
        for it in items:
            mw = {k: v for k, v in it["fields"].items() if v.get("source") == "midwife"}
            if not mw:
                continue
            if it["slot"] in by_slot:
                by_slot[it["slot"]]["fields"].update(mw)
            else:
                out["groups"][g].append(it)
    return out


def uncertain_paths(ptype: str, data: dict) -> list[str]:
    """Fields the agent must ask about: NEEDS_REVIEW, or ILLEGIBLE as read by the AI."""
    return [p for p, _, v in S.iter_fields(ptype, data)
            if v["status"] == S.NEEDS_REVIEW or (v["status"] == S.ILLEGIBLE and v["source"] == "ai")]
