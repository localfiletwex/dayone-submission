"""Field-level evaluation of the extraction pipeline against ground truth.

    python -m eval.evaluate --split dev --write-threshold
    python -m eval.evaluate --split test
    python -m eval.evaluate --photos          # 5 real photos: qualitative only

Ground truth is read HERE and nowhere else; the extractor receives image
bytes only.  The threshold is chosen on the dev split and merely reported on
the test split.
"""
from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import os
import sys
import time

from app import schema as S
from app.config import PROVISIONAL_THRESHOLD, THRESHOLD_FILE
from app.extractors.base import ExtractionError, ExtractorUnavailable
from app.extractors.gemini import prompt_version
from app.normalize import values_equal
from app.pipeline import postprocess
from eval.paths import CACHE_DIR, DEGRADED_DIR, GT_PATH, REGISTRY_DIR, RESULTS_DIR, WORK
from eval.split import SPLIT_PATH

BLANK = (S.NOT_PROVIDED, S.NOT_APPLICABLE)
BINS = (0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0001)


def _flat(page_type, data):
    return {p: (f, v) for p, f, v in S.iter_fields(page_type, data)}


def score_page(gt_page: dict, pred_type: str | None, pred_data: dict | None, meta: dict) -> list[dict]:
    """One row per ground-truth field (plus rows for values predicted in
    repeating columns that do not exist on paper)."""
    ptype = gt_page["page_type"]
    gt = _flat(ptype, gt_page)
    pred = _flat(ptype, pred_data) if pred_data and pred_type == ptype else {}
    rows = []
    for path, (f, g) in gt.items():
        if g["status"] == "AMBIGUOUS":
            continue
        p = pred.get(path, (None, None))[1]
        if p is None:
            # column not returned (=> treated as blank) or the page failed / was misclassified
            p = {"value": None, "confidence": None,
                 "status": S.NOT_PROVIDED if (pred and "[" in path) else "MISSING"}
        has_value = p["status"] in S.VALUE_STATUSES and p["value"] is not None
        filled = g["status"] == S.KNOWN
        if filled:
            correct = has_value and values_equal(f, g["value"], p["value"])
        else:
            correct = p["status"] in BLANK
        rows.append({**meta, "section": ptype, "path": path, "kind": f.kind, "mark": g.get("mark"),
                     "font": g.get("font"), "filled": filled, "gt_status": g["status"],
                     "pred_status": p["status"], "confidence": p["confidence"], "correct": bool(correct),
                     "status_exact": p["status"] == g["status"] or (filled and has_value),
                     "hallucinated": (not filled) and has_value,
                     "omitted": filled and not has_value})
    for path, (f, p) in pred.items():
        if path not in gt and p["status"] in S.VALUE_STATUSES and p["value"] is not None:
            rows.append({**meta, "section": ptype, "path": path, "kind": f.kind, "mark": "blank", "font": None,
                         "filled": False, "gt_status": S.NOT_PROVIDED, "pred_status": p["status"],
                         "confidence": p["confidence"], "correct": False, "status_exact": False,
                         "hallucinated": True, "omitted": False})
    return rows


def _acc(rows, key="correct"):
    return (sum(r[key] for r in rows) / len(rows), len(rows)) if rows else (None, 0)


def _table(rows, by, title):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[str(r.get(by))].append(r)
    out = [f"\n### {title}\n", "| group | filled acc. | n filled | blank acc. | n blank | status exact |", "|---|---|---|---|---|---|"]
    for k in sorted(groups):
        g = groups[k]
        fa, fn = _acc([r for r in g if r["filled"]])
        ba, bn = _acc([r for r in g if not r["filled"]])
        sa, _ = _acc(g, "status_exact")
        out.append(f"| {k} | {_pct(fa)} | {fn} | {_pct(ba)} | {bn} | {_pct(sa)} |")
    return "\n".join(out)


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def calibration(rows):
    scored = [r for r in rows if r["confidence"] is not None]
    bins, ece = [], 0.0
    for lo, hi in zip(BINS, BINS[1:]):
        b = [r for r in scored if lo <= r["confidence"] < hi]
        if b:
            conf = sum(r["confidence"] for r in b) / len(b)
            acc = sum(r["correct"] for r in b) / len(b)
            ece += len(b) / len(scored) * abs(conf - acc)
            bins.append({"range": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(b), "mean_confidence": conf, "accuracy": acc})
    return {"bins": bins, "ece": ece if scored else None, "n": len(scored),
            "n_without_confidence": len(rows) - len(scored)}


GRID = [i / 100 for i in range(50, 95, 5)] + [0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99, 0.995, 1.0]
MAX_ASKED_FILLED = 0.25      # above this share of filled fields asked: stop and show the options


def _accepted(r, t):
    """Would the agent store this field WITHOUT asking?  Fields that are always
    confirmed, illegible, untypable or implausible are NEEDS_REVIEW already."""
    return (r["confidence"] is not None and r["confidence"] >= t
            and r["pred_status"] not in (S.ILLEGIBLE, S.NEEDS_REVIEW, "MISSING"))


def threshold_curve(rows):
    """Silent error rate versus share of fields asked, per threshold.
    silent error = a wrong field the agent would NOT ask about."""
    out = []
    filled = [r for r in rows if r.get("filled")]
    for t in GRID:
        accepted = [r for r in rows if _accepted(r, t)]
        wrong = sum(not r["correct"] for r in accepted)
        acc_filled = sum(1 for r in filled if _accepted(r, t))
        out.append({"threshold": t, "follow_up_rate": 1 - len(accepted) / len(rows) if rows else None,
                    "asked_filled_share": 1 - acc_filled / len(filled) if filled else None,
                    "silent_error_rate": wrong / len(accepted) if accepted else None,
                    "silent_errors": wrong, "accepted": len(accepted)})
    return out


def decide_threshold(curve, target, max_asked=MAX_ASKED_FILLED):
    """Apply the agreed rule: lowest threshold with silent error <= target, PROVIDED it
    asks about at most `max_asked` of the filled fields.  Otherwise write nothing and
    return the options for a human decision."""
    chosen, met = choose_threshold(curve, target)
    share = chosen["asked_filled_share"] if chosen else None
    if chosen and met and (share is None or share <= max_asked):
        return {"write": True, "chosen": chosen, "options": []}
    options = []
    if chosen and met:
        options.append({"option": f"A - meet the {target:.0%} target", **chosen})
    elif chosen:
        options.append({"option": f"A - closest to the {target:.0%} target (NOT met at any threshold)", **chosen})
    capped = [c for c in curve if c["asked_filled_share"] is not None and c["asked_filled_share"] <= max_asked
              and c["silent_error_rate"] is not None]
    if capped:
        options.append({"option": f"B - ask about at most {max_asked:.0%} of filled fields",
                        **max(capped, key=lambda c: c["threshold"])})
    for alt in (0.03, 0.05):
        c, ok = choose_threshold(curve, alt)
        if c and ok and alt > target:
            options.append({"option": f"C - relax the target to {alt:.0%}", **c})
    return {"write": False, "chosen": chosen, "options": options}


def choose_threshold(curve, target):
    ok = [c for c in curve if c["silent_error_rate"] is not None and c["silent_error_rate"] <= target]
    if ok:
        return min(ok, key=lambda c: c["threshold"]), True
    usable = [c for c in curve if c["silent_error_rate"] is not None]
    return (min(usable, key=lambda c: (c["silent_error_rate"], c["threshold"])) if usable else None), False


LANGUAGE_NOTES = {
    "fr": "Language: French only (the dataset has no Arabic or English content).",
    "en": "Language: ENGLISH - synthetic pages made by eval/make_languages.py from the same ten patients "
          "(handwriting-style fonts, not real handwriting; translations by the tool).",
    "ar": "Language: ARABIC - synthetic pages made by eval/make_languages.py from the same ten patients "
          "(handwriting-style fonts, not real handwriting; translations by the tool; patients 6-10 use Arabic-script digits).",
}


def report(rows, split, model, page_stats, target, stored_threshold, thinking="n/a", language="fr"):
    filled = [r for r in rows if r["filled"]]
    blank = [r for r in rows if not r["filled"]]
    md = [f"# Extraction evaluation - split: {split} - model: {model} - thinking level: {thinking}"
          f" - prompt: {prompt_version()}", "",
          LANGUAGE_NOTES[language],
          "Degraded images are SYNTHETIC degradations of the clean renders.", "",
          f"Pages: {page_stats['pages']} | page type correct: {page_stats['type_correct']} | "
          f"failed: {page_stats['failed']} | API calls: {page_stats.get('api_calls')} | cache hits: {page_stats.get('cache_hits')}", ""]
    for name, subset in (("clean", [r for r in rows if r["set"] == "clean"]),
                         ("degraded (synthetic)", [r for r in rows if r["set"] == "degraded"])):
        if not subset:
            continue
        fa, fn = _acc([r for r in subset if r["filled"]])
        ba, bn = _acc([r for r in subset if not r["filled"]])
        ha = sum(r["hallucinated"] for r in subset)
        om = sum(r["omitted"] for r in subset)
        md.append(f"**{name}**: filled-field accuracy {_pct(fa)} (n={fn}); blank-field accuracy {_pct(ba)} (n={bn}); "
                  f"values invented on blank cells: {ha}; filled cells returned as blank/illegible: {om}")
    for s in ("clean", "degraded"):
        sub = [r for r in rows if r["set"] == s]
        if sub:
            md.append(_table(sub, "section", f"By section - {s}"))
            md.append(_table(sub, "mark", f"Handwritten vs checkbox vs blank - {s} (no printed values exist in the data)"))
            md.append(_table(sub, "font", f"By handwriting style (font) - {s}"))
    if any(r["set"] == "degraded" for r in rows):
        md.append(_table([r for r in rows if r["set"] == "degraded"], "degradation", "By synthetic degradation type"))
    cal = calibration(rows)
    md += ["\n### Confidence vs correctness\n", "| confidence | n | mean confidence | accuracy |", "|---|---|---|---|"]
    md += [f"| {b['range']} | {b['n']} | {b['mean_confidence']:.3f} | {_pct(b['accuracy'])} |" for b in cal["bins"]]
    md.append(f"\nExpected calibration error: {cal['ece']:.4f} over {cal['n']} fields "
              f"({cal['n_without_confidence']} fields had no confidence)." if cal["ece"] is not None else "\nNo confidences.")
    curve = threshold_curve(rows)
    md += ["\n### Silent error rate versus share of fields asked\n",
           "Fields that are always confirmed (linking code, HIV, syphilis, HBsAg results) count as asked.",
          "Cells of a table column that the model did not return also count as asked: for every table the agent "
          "asks whether another column is filled on paper (they are still errors of the model, counted in the accuracy above).\n",
           "| threshold | filled fields asked | all fields asked | silent error rate | silent errors / stored without asking |",
           "|---|---|---|---|---|"]
    md += [f"| {c['threshold']:.3f} | {_pct(c['asked_filled_share'])} | {_pct(c['follow_up_rate'])} | "
           f"{_pct(c['silent_error_rate'])} | {c['silent_errors']} / {c['accepted']} |" for c in curve]
    decision = decide_threshold(curve, target)
    chosen, met = choose_threshold(curve, target)
    if stored_threshold is not None:
        at = min(curve, key=lambda c: abs(c["threshold"] - stored_threshold))
        md.append(f"\n**At the threshold chosen on dev ({stored_threshold:.3f})**, on this split: filled fields asked "
                  f"{_pct(at['asked_filled_share'])}, silent error rate {_pct(at['silent_error_rate'])} "
                  f"({at['silent_errors']} / {at['accepted']}).")
    elif decision["write"]:
        md.append(f"\nLowest threshold with silent error rate <= {_pct(target)}: **{chosen['threshold']:.3f}** "
                  f"(asks about {_pct(chosen['asked_filled_share'])} of filled fields).")
    else:
        md += [f"\n**STOP - no threshold written.** Meeting {_pct(target)} would mean asking about more than "
               f"{_pct(MAX_ASKED_FILLED)} of filled fields, or the target cannot be met. Options:\n",
               "| option | threshold | filled fields asked | silent error rate |", "|---|---|---|---|"]
        md += [f"| {o['option']} | {o['threshold']:.3f} | {_pct(o['asked_filled_share'])} | {_pct(o['silent_error_rate'])} |"
               for o in decision["options"]]
    summary = {"split": split, "model": model, "thinking_level": thinking, "pages": page_stats,
               "threshold_decision": decision, "stored_threshold": stored_threshold,
               "filled_accuracy": _acc(filled)[0], "n_filled": len(filled),
               "blank_accuracy": _acc(blank)[0], "n_blank": len(blank),
               "calibration": cal, "threshold_curve": curve,
               "chosen_threshold": chosen, "target_met": met, "target": target}
    return "\n".join(md), summary


def arabic_options(rows, base: float) -> str:
    """What the optional stricter threshold of the app (pipeline.finalize) would do on these rows:
    on a page with Arabic entries, filled fields read as KNOWN below T are asked about, either the
    numbers only or every field.  Same definitions as the curve above."""
    filled = [r for r in rows if r.get("filled")]
    out = ["", "### Stricter threshold on pages with Arabic entries (an option of the app, off unless set)", "",
           f"General threshold {base:.2f}. Every page of this set has Arabic entries.", "",
           "| applied to | threshold | filled fields asked | silent error rate | silent errors / stored without asking |",
           "|---|---|---|---|---|"]
    for scope, kinds in (("-", ()), ("numbers", S.NUMERIC_KINDS), ("every filled field", None)):
        for t in ((base,) if scope == "-" else (0.85, 0.92)):
            def stored(r):
                strict = r["pred_status"] == S.KNOWN and (kinds is None or r["kind"] in kinds)
                return _accepted(r, t if strict else base)
            acc = [r for r in rows if stored(r)]
            wrong = sum(not r["correct"] for r in acc)
            asked = 1 - sum(1 for r in filled if stored(r)) / len(filled) if filled else 0
            out.append(f"| {'not set' if scope == '-' else scope} | {t:.2f} | {asked:.1%} | "
                       f"{wrong / len(acc) if acc else 0:.1%} | {wrong} / {len(acc)} |")
    return "\n".join(out)


def make_extractor(name, min_interval):
    if name == "fake":
        from app.extractors.fake import FakeExtractor
        return FakeExtractor()
    from app.extractors.gemini import GeminiExtractor
    return GeminiExtractor(cache_dir=CACHE_DIR, min_interval=min_interval)


def run_images(extractor, items, gt):
    rows, stats = [], {"pages": 0, "type_correct": 0, "failed": 0}
    for i, (path, meta) in enumerate(items, 1):
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        stats["pages"] += 1
        ptype = data = None
        try:
            raw = extractor.extract(path.read_bytes(), mime)
            ptype, data, _ = postprocess(raw, threshold=0.0)   # threshold 0: keep the model's own statuses
        except ExtractorUnavailable as e:
            sys.exit(f"extractor unavailable ({e}). Re-run the same command: finished pages are cached.")
        except (ExtractionError, ValueError) as e:
            stats["failed"] += 1
            print(f"  [{i}/{len(items)}] {path.name}: FAILED ({e})", flush=True)
        gt_page = gt[str(meta["page"])]
        stats["type_correct"] += ptype == gt_page["page_type"]
        rows += score_page(gt_page, ptype, data, meta)
        if i % 10 == 0:
            print(f"  [{i}/{len(items)}] done", flush=True)
    stats.update(getattr(extractor, "stats", {}))
    return rows, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    ap.add_argument("--set", choices=("clean", "degraded", "both"), default="both")
    ap.add_argument("--extractor", choices=("gemini", "fake"), default="gemini")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages (smoke test)")
    ap.add_argument("--target", type=float, default=0.02, help="max silent error rate for the threshold")
    ap.add_argument("--write-threshold", action="store_true")
    ap.add_argument("--min-interval", type=float, default=float(os.environ.get("GEMINI_MIN_INTERVAL", "0")))
    ap.add_argument("--photos", action="store_true", help="qualitative run on the 5 real photos")
    ap.add_argument("--clean-only", action="store_true", help="clean images only (same as --set clean)")
    ap.add_argument("--smoke", action="store_true", help="ONE call to check key, model id and thinking level")
    ap.add_argument("--set-arabic-threshold", type=float, metavar="T",
                    help="store T as the stricter threshold for pages with Arabic entries (0 removes it), then exit")
    ap.add_argument("--arabic-scope", choices=("numbers", "all"), default="numbers",
                    help="with --set-arabic-threshold: apply it to the numbers of such pages (default) or to every filled field")
    ap.add_argument("--language", choices=("fr", "en", "ar"), default="fr",
                    help="fr: the dataset. en / ar: the synthetic pages of eval/make_languages.py")
    a = ap.parse_args()
    if a.clean_only:
        a.set = "clean"
    if a.set_arabic_threshold is not None:
        # A human decision, recorded as such: on the Arabic dev pages no threshold meets the 2% target
        # while asking about at most 25% of the filled fields, so the evaluation cannot choose one itself.
        if not THRESHOLD_FILE.exists():
            sys.exit(f"{THRESHOLD_FILE} does not exist yet: run scripts/run_eval.sh first.")
        d = json.loads(THRESHOLD_FILE.read_text())
        for k in [k for k in d if k.startswith("threshold_arabic")]:
            del d[k]
        if a.set_arabic_threshold > 0:
            d["threshold_arabic"] = a.set_arabic_threshold
            d["threshold_arabic_scope"] = a.arabic_scope
            d["threshold_arabic_note"] = "chosen from the table at the end of eval-dev-...-ar-...md"
        THRESHOLD_FILE.write_text(json.dumps(d, indent=1))
        print(f"{THRESHOLD_FILE}: on pages with Arabic entries, " + (
            f"{'numbers' if a.arabic_scope == 'numbers' else 'every filled field'} below {a.set_arabic_threshold} are asked about"
            if a.set_arabic_threshold > 0 else "no stricter threshold"))
        return
    extractor = make_extractor(a.extractor, a.min_interval)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model = getattr(extractor, "model", extractor.name)
    thinking = getattr(extractor, "thinking_level", "n/a")
    if a.smoke:
        res = extractor.ping() if hasattr(extractor, "ping") else {"ok": True, "detail": "fake extractor"}
        print(f"smoke test: model={model} thinking_level={thinking} -> {'OK' if res['ok'] else 'FAILED'}: {res['detail']}")
        if res.get("model_version"):
            print(f"  served model version: {res['model_version']}")
        sys.exit(0 if res["ok"] else 2)
    if a.photos:
        out = {}
        for p in sorted(REGISTRY_DIR.glob("1-*.jpg")):
            try:
                ptype, data, info = postprocess(extractor.extract(p.read_bytes(), "image/jpeg"), threshold=0.0)
                out[p.name] = {"page_type": ptype, "info": info,
                               "fields": {path: [v["value"], v["status"], v["confidence"]]
                                          for path, _, v in S.iter_fields(ptype, data)}}
            except (ExtractionError, ValueError) as e:
                out[p.name] = {"error": str(e)}
        dest = RESULTS_DIR / "real-photos-qualitative.json"
        dest.write_text(json.dumps({"model": model, "thinking_level": thinking, "photos": out}, ensure_ascii=False, indent=1))
        print(f"wrote {dest} (no ground truth exists for these photos: inspect by eye)")
        return
    gt_path = GT_PATH if a.language == "fr" else GT_PATH.with_name(f"gt-{a.language}.json")
    if not gt_path.exists():
        sys.exit(f"{gt_path} not found. Run  python -m eval.make_languages  (or unzip dayone-languages.zip) first.")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    split = json.loads(SPLIT_PATH.read_text())
    pages = [int(n) for n, m in split["pages"].items() if a.split == "all" or m["split"] == a.split]
    if a.limit:
        pages = pages[:a.limit]
    items = []
    if a.language != "fr":                      # synthetic English / Arabic pages: clean images only
        if a.write_threshold:
            sys.exit("--write-threshold is only allowed on the French dataset")
        a.set = "clean"
        items = [(WORK / "languages" / a.language / f"page-{n:02d}.png",
                  {"page": n, "patient": split["pages"][str(n)]["patient"], "set": "clean",
                   "degradation": "none", "language": a.language}) for n in pages]
    elif a.set in ("clean", "both"):
        items += [(REGISTRY_DIR / split["pages"][str(n)]["file"],
                   {"page": n, "patient": split["pages"][str(n)]["patient"], "set": "clean",
                    "degradation": "none", "language": "fr"}) for n in pages]
    if a.language == "fr" and a.set in ("degraded", "both"):
        man = json.loads((DEGRADED_DIR / "manifest.json").read_text())
        items += [(DEGRADED_DIR / name, {"page": m["page"], "patient": split["pages"][str(m["page"])]["patient"],
                                         "set": "degraded", "degradation": m["degradation"], "language": "fr"})
                  for name, m in sorted(man.items()) if m["page"] in pages]
    print(f"evaluating {len(items)} images ({a.split} split, extractor={a.extractor})", flush=True)
    t0 = time.time()
    rows, stats = run_images(extractor, items, gt)
    saved = json.loads(THRESHOLD_FILE.read_text()) if THRESHOLD_FILE.exists() else {}
    stored = saved.get("threshold")

    md, summary = report(rows, a.split, model, stats, a.target, None if a.write_threshold else stored, thinking,
                         a.language)
    if a.language == "ar":
        md += "\n" + arabic_options(rows, stored if stored is not None else PROVISIONAL_THRESHOLD)
    summary["image_set"] = a.set
    tag = f"{a.split}-{model}-think-{thinking}".replace("/", "_") + ("-clean" if a.set == "clean" else "")
    if a.language != "fr":                      # one file per language AND prompt, so that v1 and v2 can be compared
        tag += f"-{a.language}-prompt-{prompt_version()}"
    import csv
    with open(RESULTS_DIR / f"curve-{tag}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["model", "thinking_level", "split", *summary["threshold_curve"][0].keys()])
        w.writeheader()
        for c in summary["threshold_curve"]:
            w.writerow({"model": model, "thinking_level": thinking, "split": a.split, **c})
    (RESULTS_DIR / f"eval-{tag}.md").write_text(md)
    (RESULTS_DIR / f"eval-{tag}.json").write_text(json.dumps(summary, indent=1))
    (RESULTS_DIR / f"eval-{tag}-rows.json").write_text(json.dumps(rows))
    print(md)
    print(f"\n({time.time() - t0:.0f}s) wrote {RESULTS_DIR}/eval-{tag}.md/.json")
    if a.write_threshold:
        if a.split != "dev" or a.extractor == "fake":
            sys.exit("--write-threshold is only allowed with --split dev and a real extractor")
        if not summary["threshold_decision"]["write"]:
            if THRESHOLD_FILE.exists():
                THRESHOLD_FILE.unlink()               # never leave a stale threshold from another run
            print("\nSTOP: no threshold was written (see the options table above). "
                  "The app keeps its provisional threshold until you choose.")
            sys.exit(3)
        ch = summary["chosen_threshold"]
        THRESHOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
        THRESHOLD_FILE.write_text(json.dumps({
            **{k: v for k, v in saved.items() if k.startswith("threshold_arabic")},     # a hand-set choice survives
            "threshold": ch["threshold"], "chosen_on": f"dev patients ({a.set} images)",
            "target_silent_error_rate": a.target, "silent_error_rate": ch["silent_error_rate"],
            "asked_filled_share": ch["asked_filled_share"], "follow_up_rate": ch["follow_up_rate"],
            "model": model, "thinking_level": thinking, "prompt": prompt_version()}, indent=1))
        print(f"wrote {THRESHOLD_FILE}: threshold = {ch['threshold']}")


if __name__ == "__main__":
    main()
