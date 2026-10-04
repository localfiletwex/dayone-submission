"""Build evaluation ground truth from the registry PDF's text layer and
vector drawing.  EVALUATION ONLY: nothing under app/ imports this module and
the extraction pipeline never sees the PDF or the output of this script.

Handwritten values are the non-Helvetica glyphs; checkbox state is read from
the vector paths (a small square with ink strokes crossing it is ticked).

    python -m eval.build_ground_truth
"""
from __future__ import annotations

import collections
import json
import sys

import pymupdf

from app import schema as S
from app.normalize import WILDCARD, canon
from eval.paths import GT_PATH, REGISTRY_PDF, WORK

# printed labels whose handwritten value is a direct identifier or an examiner
# name: intentionally not modelled, listed so the coverage report can tell
# "ignored on purpose" from "missed".
IGNORED_LABELS = ("Nom/Prénom de la parturiente :", "CIN :", "Adresse :", "Téléphone :",
                  "Nom du Mari :", "Patiente :", "Vu par :", "Examen fait par")


def _cluster_lines(items, tol=5.0):
    """Sort items (with .cy/.x0) into reading order."""
    items = sorted(items, key=lambda i: i["cy"])
    line, last = 0, None
    for it in items:
        if last is not None and it["cy"] - last > tol:
            line += 1
        it["line"], last = line, it["cy"]
    return sorted(items, key=lambda i: (i["line"], i["x0"]))


class Page:
    def __init__(self, pg):
        self.labels, self.chars, self.boxes, ink = [], [], [], []
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    if not s["text"].strip():
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    it = {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "cy": (y0 + y1) / 2,
                          "text": s["text"].strip(), "size": s["size"], "font": s["font"].split("+")[-1]}
                    if "Helvetica" in s["font"]:
                        self.labels.append(it)
                    else:
                        it["text"] = s["text"].replace("\x00", WILDCARD)
                        it["used"] = False
                        self.chars.append(it)
        self.labels = _cluster_lines(self.labels)
        for d in pg.get_drawings():
            r = d["rect"]
            if len(d["items"]) == 4 and 6 <= r.width <= 11 and 6 <= r.height <= 11:
                self.boxes.append({"rect": r, "cy": (r.y0 + r.y1) / 2, "x0": r.x0, "x1": r.x1})
            elif len(d["items"]) <= 3 and r.width < 22 and r.height < 22 and d.get("fill") is None:
                ink.append(r)
        for b in self.boxes:
            grown = b["rect"] + (-1.5, -1.5, 1.5, 1.5)
            b["ticked"] = any(i.intersects(grown) for i in ink)
            b["label"] = self._box_label(b)
        self.blank = not self.chars and not any(b["ticked"] for b in self.boxes)

    def _box_label(self, b):
        right = [l for l in self.labels if abs(l["cy"] - b["cy"]) < 6 and 0 <= l["x0"] - b["x1"] <= 14]
        if right:
            return min(right, key=lambda l: l["x0"])
        left = [l for l in self.labels if abs(l["cy"] - b["cy"]) < 6 and 0 <= b["x0"] - l["x1"] <= 90]
        return max(left, key=lambda l: l["x1"]) if left else None

    def label(self, text, n=1):
        hits = [l for l in self.labels if l["text"] == text]
        if not hits:
            hits = [l for l in self.labels if l["text"].startswith(text)]
        return hits[n - 1] if len(hits) >= n else None

    def box_for(self, text, n=1):
        lab = self.label(text, n)
        if lab is None:
            return None
        for b in self.boxes:
            if b["label"] is lab:
                return b
        return None

    def text_in(self, x0, x1, y0, y1):
        cs = [c for c in self.chars if x0 <= (c["x0"] + c["x1"]) / 2 < x1 and y0 <= c["cy"] < y1]
        if not cs:
            return None, None
        cs = _cluster_lines(cs, tol=6.0)
        out, prev = "", None
        for c in cs:
            c["used"] = True
            if prev is not None and (c["x0"] - prev["x1"] > 0.2 * c["size"] or c["line"] != prev["line"]):
                out += " "
            out += c["text"]
            prev = c
        return out.strip(), cs[0]["font"]

    def kv(self, text, n=1):
        lab = self.label(text, n)
        if lab is None:
            return "MISSING", None
        nxt = [l["x0"] for l in self.labels if abs(l["cy"] - lab["cy"]) < 7 and l["x0"] > lab["x1"]]
        return self.text_in(lab["x1"] - 1, min(nxt) - 1 if nxt else 1e9, lab["cy"] - 8, lab["cy"] + 8)


def _band(labs, i, axis0, lo_pad):
    """Band of label i among sorted sibling labels along one axis."""
    pos = [l[axis0] for l in labs]
    pitch = min([abs(pos[j + 1] - pos[j]) for j in range(len(pos) - 1)] or [60])
    start = pos[i] - lo_pad
    end = pos[i + 1] - lo_pad if i + 1 < len(pos) else pos[i] + pitch - lo_pad
    return start, end


def _text_field(f: S.Field, raw, font):
    if raw == "MISSING":
        return None
    if raw is None or raw == "":
        return {"value": None, "status": S.NOT_PROVIDED, "mark": "blank"}
    if set(raw) <= {WILDCARD, " "}:
        # glyph missing from the handwriting font: nothing is drawn in the image
        return {"value": None, "status": S.NOT_PROVIDED, "mark": "unrendered"}
    if raw in S.STRIKE_MARKS:
        return {"value": None, "status": S.NOT_APPLICABLE, "mark": "struck"}
    val, ok = canon(f, raw)
    return {"value": val, "status": S.KNOWN, "mark": "handwritten", "font": font,
            "raw": raw, "typed": ok, "wildcard": WILDCARD in raw}


def _section_fields(pg: Page, sec: S.Section, missing: list):
    out = {}
    for f in sec.fields:
        loc = f.loc or (("opts", (1,) * len(f.options)) if f.kind in (S.CHOICE, S.MULTI) else ("kv", 1))
        res = None
        if loc[0] == "kv":
            res = _text_field(f, *pg.kv(f.label, loc[1]))
        elif loc[0] == "under":
            lab = pg.label(f.label, loc[1])
            if lab is not None:
                res = _text_field(f, *pg.text_in(lab["x0"] - 6, 1e9, lab["y1"], lab["y1"] + 30))
        elif loc[0] == "below":
            heads = [pg.label(x.label) for x in sec.fields if x.loc and x.loc[0] == "below"]
            if all(heads):
                heads.sort(key=lambda l: l["x0"])
                i = next(i for i, h in enumerate(heads) if h["text"] == f.label)
                x0, x1 = _band(heads, i, "x0", 6)
                res = _text_field(f, *pg.text_in(x0, x1, heads[i]["y1"], heads[i]["y1"] + 90))
        elif loc[0] == "cell":
            sib = [x for x in sec.fields if x.loc and x.loc[0] == "cell" and
                   (x.loc[2] == loc[2] or x.loc[1] == loc[1])]
            rows = sorted({x.loc[1] for x in sec.fields if x.loc and x.loc[0] == "cell" and x.loc[2] == loc[2]})
            cols = sorted({x.loc[2] for x in sec.fields if x.loc and x.loc[0] == "cell" and x.loc[1] == loc[1]})
            rl = [pg.label(r) for r in rows]
            cl = [pg.label(c) for c in cols]
            if all(rl) and all(cl) and sib:
                rl.sort(key=lambda l: l["cy"])
                cl.sort(key=lambda l: l["x0"])
                ri = next(i for i, l in enumerate(rl) if l["text"].startswith(loc[1]))
                ci = next(i for i, l in enumerate(cl) if l["text"].startswith(loc[2]))
                x0, x1 = _band(cl, ci, "x0", 5)
                pitch = min([rl[j + 1]["cy"] - rl[j]["cy"] for j in range(len(rl) - 1)] or [20])
                res = _text_field(f, *pg.text_in(x0, x1, rl[ri]["cy"] - pitch / 2, rl[ri]["cy"] + pitch / 2))
        elif loc[0] == "box":
            b = pg.box_for(f.label, loc[1])
            if b is not None:
                res = {"value": S.TICKED if b["ticked"] else S.NOT_TICKED, "status": S.KNOWN, "mark": "checkbox"}
        elif loc[0] == "opts":
            bs = [pg.box_for(o, n) for o, n in zip(f.options, loc[1])]
            if all(b is not None for b in bs):
                ticked = [o for o, b in zip(f.options, bs) if b["ticked"]]
                if f.kind == S.MULTI:
                    res = {"value": ticked, "status": S.KNOWN, "mark": "checkbox"}
                elif len(ticked) == 1:
                    res = {"value": ticked[0], "status": S.KNOWN, "mark": "checkbox"}
                elif not ticked:
                    res = {"value": None, "status": S.NOT_PROVIDED, "mark": "checkbox"}
                else:
                    res = {"value": None, "status": "AMBIGUOUS", "mark": "checkbox", "raw": ticked}
        if res is None:
            missing.append(f"{sec.key}.{f.key}")
            continue
        if pg.blank:
            res = {"value": None, "status": S.NOT_PROVIDED, "mark": "blank"}
        res["kind"] = f.kind
        out[f.key] = res
    return out


def _section_groups(pg: Page, sec: S.Section, missing: list):
    groups = {}
    for g in sec.groups:
        heads = [pg.label(lab, n) for _, lab, n in g.slots]
        rows = [pg.label(f.loc[1], f.loc[2]) for f in g.fields]
        if not all(heads) or not all(rows):
            missing.append(f"{sec.key}.{g.name}[*]")
            continue
        order = sorted(range(len(heads)), key=lambda i: heads[i]["x0"])
        hs = [heads[i] for i in order]
        ys = sorted(r["cy"] for r in rows)
        pitch = min(ys[j + 1] - ys[j] for j in range(len(ys) - 1))
        items = []
        for rank, i in enumerate(order):
            x0, x1 = _band(hs, rank, "x0", 4)
            fields = {}
            for f, r in zip(g.fields, rows):
                res = _text_field(f, *pg.text_in(x0, x1, r["cy"] - pitch / 2, r["cy"] + pitch / 2))
                res["kind"] = f.kind
                fields[f.key] = res
            items.append({"slot": g.slots[i][0], "fields": fields})
        items.sort(key=lambda it: [s[0] for s in g.slots].index(it["slot"]))
        groups[g.name] = items
    return groups


def build():
    doc = pymupdf.open(REGISTRY_PDF)
    gt, missing, stray = {}, [], collections.Counter()
    for i, pg in enumerate(doc):
        sec = S.SECTIONS[i % 8]
        p = Page(pg)
        m = []
        data = {"fields": _section_fields(p, sec, m), "groups": _section_groups(p, sec, m)}
        # mark identifier / examiner handwriting as intentionally ignored
        for lab_text in IGNORED_LABELS:
            for n in (1, 2):
                if p.label(lab_text, n):
                    p.kv(lab_text, n)
        if sec.key == "identification":
            p.kv("Profession :", 2)                      # husband's occupation: not modelled
        if sec.key == "pregnancy":
            lab = p.label("Examen fait par", 2) or p.label("Examen fait par", 1)
            if lab:
                p.text_in(lab["x1"], 1e9, lab["cy"] - 8, lab["cy"] + 8)
        for c in p.chars:
            if not c["used"]:
                stray[(i + 1, round(c["cy"] / 10) * 10)] += 1
        S.apply_blank_rules(sec.key, data)               # shared blank-cell rule
        for _, f, v in S.iter_fields(sec.key, data):
            v.setdefault("kind", f.kind)
            v.setdefault("mark", "dependency")
        gt[str(i + 1)] = {"page": i + 1, "patient": i // 8 + 1, "page_type": sec.key,
                          "blank_page": p.blank, **data}
        missing += [f"p{i + 1}:{x}" for x in m]
    return gt, missing, stray


def main():
    gt, missing, stray = build()
    WORK.mkdir(parents=True, exist_ok=True)
    GT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GT_PATH.write_text(json.dumps(gt, ensure_ascii=False, indent=1))
    n = collections.Counter()
    for pg in gt.values():
        for _, _, v in S.iter_fields(pg["page_type"], pg):
            n[(v.get("mark"), v["status"])] += 1
    print(f"wrote {GT_PATH}  ({len(gt)} pages)")
    for k, v in sorted(n.items(), key=str):
        print(f"  {str(k[0]):<12} {k[1]:<15} {v}")
    no_gt = sorted({m.split(':', 1)[1] for m in missing})
    print(f"fields with NO ground truth ({len(no_gt)} distinct, {len(missing)} page-fields):")
    for m in no_gt:
        print("   ", m, "x", sum(1 for x in missing if x.endswith(':' + m)))
    print(f"unassigned handwritten glyphs: {sum(stray.values())} in {len(stray)} places", dict(list(stray.items())[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
