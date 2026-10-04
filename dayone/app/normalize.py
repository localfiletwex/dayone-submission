"""Value canonicalisation and plausibility validation.

Used by the extraction post-processing (to store typed values and to flag
implausible ones for review) and by the evaluation script (to compare
predictions with ground truth).  Nothing here ever invents or "repairs" a
value: an unparseable or implausible value is kept verbatim and flagged.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import languages, schema as S

_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_DATE = re.compile(r"^\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_BP = re.compile(r"^\s*(\d{2,3})\s*[/\\|]\s*(\d{2,3})\s*$")
WILDCARD = "�"   # a character the source could not render


def fold(text) -> str:
    """Accent-, case-, space- and punctuation-insensitive comparison key."""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("œ", "oe").replace("æ", "ae")
    return "".join(c for c in t if c.isalnum() or c == WILDCARD)


def match_option(raw, options):
    """Map a raw string onto one of the printed options (exact after folding)."""
    exact = str(raw).strip().casefold()
    for o in options:
        if o.casefold() == exact:
            return o
    hits = [o for o in options if fold(o) == fold(raw)]     # "Rh+" and "Rh-" fold alike:
    return hits[0] if len(hits) == 1 else None               # only accept an unambiguous match


def canon(field: S.Field, raw):
    """Return (canonical_value, ok).  ok=False means 'could not be typed';
    the caller keeps the verbatim value and asks the midwife.

    Entries may be written in French, English or Arabic (app/languages.py):
    Arabic-script digits and punctuation are first mapped to the Western ones,
    and a date with a month NAME ("12 mai 2022", "May 12, 2022", "١٢ ماي ٢٠٢٢")
    to a numeric one.  After that, every language is typed by the same code.
    """
    if raw is None:
        return None, True
    k = field.kind
    if isinstance(raw, (list, tuple)):
        raw = [languages.western_digits(x) for x in raw]
    elif isinstance(raw, str):
        raw = languages.western_digits(raw)
        if k == S.DATE:
            raw = languages.numeric_date(raw) or raw
    if k == S.BOOL:
        if isinstance(raw, bool):
            return (S.TICKED if raw else S.NOT_TICKED), True
        f = fold(raw)
        if f in ("true", "oui", "yes", "1", "ticked", "coche", "cochee", "x"):
            return S.TICKED, True
        if f in ("false", "non", "no", "0", "unticked", "notticked", "noncoche", "noncochee"):
            return S.NOT_TICKED, True
        return raw, False
    if k == S.CHOICE:
        o = match_option(raw, field.options)
        return (o, True) if o else (str(raw).strip(), False)
    if k == S.MULTI:
        items = raw if isinstance(raw, (list, tuple)) else re.split(r"[;,]", str(raw))
        out, ok = [], True
        for it in items:
            if str(it).strip() == "":
                continue
            o = match_option(it, field.options)
            if o:
                out.append(o)
            else:
                ok = False
        return [o for o in field.options if o in out], ok
    s = " ".join(str(raw).split())
    if s == "":
        return None, True
    if k == S.DATE:
        m = _ISO.match(s)
        if m:
            y, mo, d = map(int, m.groups())
        else:
            m = _DATE.match(s)
            if not m:
                return s, False
            d, mo, y = map(int, m.groups())
            if y < 100:
                y += 2000
        try:
            return date(y, mo, d).isoformat(), True
        except ValueError:
            return s, False
    if k in (S.INT, S.NUM):
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            v = float(raw)
        else:
            nums = _NUM.findall(s)
            if len(nums) != 1:
                return s, False
            v = float(nums[0].replace(",", "."))
        if k == S.INT:
            return (int(v), True) if v == int(v) else (s, False)
        return (int(v) if v == int(v) else v), True
    if k == S.BP:
        m = _BP.match(s)
        return (f"{int(m.group(1))}/{int(m.group(2))}", True) if m else (s, False)
    return s, True


def plausible(field: S.Field, value) -> bool:
    """Range check for numeric fields.  False => flag for review."""
    if field.kind in (S.INT, S.NUM) and isinstance(value, (int, float)) and not isinstance(value, bool):
        if field.lo is not None and value < field.lo:
            return False
        if field.hi is not None and value > field.hi:
            return False
    if field.kind == S.BP and isinstance(value, str) and "/" in value:
        try:
            s, d = (int(x) for x in value.split("/"))
        except ValueError:
            return False
        return 50 <= s <= 260 and 30 <= d <= 160 and d < s
    if field.kind == S.DATE and isinstance(value, str) and _ISO.match(value):
        return 1990 <= int(value[:4]) <= 2100
    return True


def values_equal(field: S.Field, truth, pred) -> bool:
    """Equality used by the evaluation.  `truth` may contain WILDCARD."""
    if truth is None or pred is None:
        return truth is None and pred is None
    k = field.kind
    if k == S.BOOL:
        return truth == pred
    if k == S.CHOICE:
        return str(truth).strip().casefold() == str(pred).strip().casefold()
    if k == S.MULTI:
        return sorted(map(fold, truth)) == sorted(map(fold, pred if isinstance(pred, list) else [pred]))
    if k in (S.INT, S.NUM) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (truth, pred)):
        return abs(float(truth) - float(pred)) < 1e-6
    a, b = fold(truth), fold(pred)
    if WILDCARD in a:
        return re.fullmatch(".?".join(map(re.escape, a.split(WILDCARD))), b) is not None
    return a == b


def display(field: S.Field, value) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(value) if value else "none ticked"
    if field.kind == S.DATE and isinstance(value, str) and _ISO.match(value):
        y, m, d = value.split("-")
        return f"{d}/{m}/{y}"
    unit = f" {field.unit}" if field.unit and isinstance(value, (int, float)) else ""
    return f"{value}{unit}"
