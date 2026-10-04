"""What the APP would store without asking, replayed from the model's saved answers.

    python -m eval.replay                 # every language and split found in the cache; no model call

The evaluation report gives the silent-error curve per threshold.  This goes
one step further: it pushes each cached answer through the app's own
`pipeline.postprocess`, with the thresholds the app is configured with
(config/threshold.json, including the optional stricter threshold for pages
with Arabic entries), and counts

* wrong values stored without a question (a field that ends KNOWN, blank,
  not applicable or unknown, and differs from the reference), and
* the share of filled fields the midwife is asked about (uncertain fields,
  plus the cells of a table column the model did not return: the agent asks
  whether another column is filled).

Pages that are not in the cache are skipped and counted, never sent to the model.
"""
from __future__ import annotations

import json

from app import config, schema as S
from app.extractors.gemini import GeminiExtractor
from app.normalize import values_equal
from app.pipeline import postprocess
from eval import evaluate as E
from eval.paths import CACHE_DIR, GT_PATH, REGISTRY_DIR, WORK
from eval.split import SPLIT_PATH


class _CacheOnly(GeminiExtractor):
    """Answers from the cache; a page that is not cached raises instead of calling the model."""

    def _call(self, prompt, image, mime):
        raise LookupError("not in the cache")


def replay(lang: str, which: str, threshold: float, threshold_arabic=None, arabic_scope="numbers") -> dict:
    gt_path = GT_PATH if lang == "fr" else GT_PATH.with_name(f"gt-{lang}.json")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    split = json.loads(SPLIT_PATH.read_text())
    ex = _CacheOnly(api_key="cache-only", cache_dir=CACHE_DIR, max_retries=0)
    out = {"pages": 0, "missing": 0, "stored": 0, "silent": 0, "filled": 0, "asked_filled": 0}
    for n, m in split["pages"].items():
        if m["split"] != which:
            continue
        path = REGISTRY_DIR / m["file"] if lang == "fr" else WORK / "languages" / lang / f"page-{int(n):02d}.png"
        try:
            raw = ex.extract(path.read_bytes(), "image/png")
        except (LookupError, OSError):
            out["missing"] += 1
            continue
        out["pages"] += 1
        ptype, data, _ = postprocess(raw, threshold, threshold_arabic, arabic_scope)
        ref, got = E._flat(ptype, gt[n]), E._flat(ptype, data)
        for key, (f, g) in ref.items():
            if g["status"] == "AMBIGUOUS":
                continue
            p = got.get(key, (None, None))[1]
            filled = g["status"] == S.KNOWN
            out["filled"] += filled
            if p is None or p["status"] in (S.NEEDS_REVIEW, S.ILLEGIBLE):
                out["asked_filled"] += filled
                continue
            out["stored"] += 1
            has_value = p["status"] in S.VALUE_STATUSES and p["value"] is not None
            right = (has_value and values_equal(f, g["value"], p["value"])) if filled else not has_value
            out["silent"] += not right
    return out


def main():
    t = config.load_threshold()
    option = (f"pages with Arabic entries: {'numbers' if t['arabic_scope'] == 'numbers' else 'every filled field'} "
              f"below {t['arabic']}" if t["arabic"] else "no stricter threshold for pages with Arabic entries")
    print(f"general threshold {t['value']} ({'calibrated' if t['calibrated'] else 'provisional'}); {option}\n")
    print("| language | patients | pages | wrong values stored without asking | filled fields asked |")
    print("|---|---|---|---|---|")
    for lang in ("fr", "en", "ar"):
        if lang != "fr" and not GT_PATH.with_name(f"gt-{lang}.json").exists():
            continue
        for which in ("dev", "test"):
            r = replay(lang, which, t["value"], t["arabic"], t["arabic_scope"])
            if not r["pages"]:
                continue
            note = f" ({r['missing']} not in the cache)" if r["missing"] else ""
            print(f"| {lang} | {which} | {r['pages']}{note} | {r['silent'] / r['stored']:.1%} ({r['silent']} / {r['stored']}) "
                  f"| {r['asked_filled'] / r['filled']:.1%} |")


if __name__ == "__main__":
    main()
