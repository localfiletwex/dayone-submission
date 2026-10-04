"""Deduplicate images by content hash and split BY PATIENT (never by page).

    python -m eval.split
"""
from __future__ import annotations

import hashlib
import json
import random
import re

from eval.paths import REGISTRY_DIR, WORK

SEED = 17
TEST_PATIENTS = 4            # of 10; the rest are the dev split
SPLIT_PATH = WORK / "split.json"


def unique_pages():
    """Return {page_no: path} with one file per distinct content hash."""
    by_hash, dupes = {}, 0
    for p in sorted(REGISTRY_DIR.glob("dossiers_specimen_10_patientes-*.png")):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h in by_hash:
            dupes += 1
            continue
        by_hash[h] = p
    pages = {}
    for h, p in by_hash.items():
        n = int(re.search(r"-(\d+)(?:__|\.)", p.name).group(1))
        if n in pages:
            raise RuntimeError(f"page {n} has two different images: {pages[n].name}, {p.name}")
        pages[n] = p
    return pages, dupes


def make_split():
    pages, dupes = unique_pages()
    patients = list(range(1, 11))
    random.Random(SEED).shuffle(patients)
    test = sorted(patients[:TEST_PATIENTS])
    dev = sorted(patients[TEST_PATIENTS:])
    split = {"seed": SEED, "duplicates_removed": dupes, "dev_patients": dev, "test_patients": test,
             "pages": {str(n): {"file": p.name, "patient": (n - 1) // 8 + 1,
                                "split": "test" if (n - 1) // 8 + 1 in test else "dev"}
                       for n, p in sorted(pages.items())}}
    WORK.mkdir(parents=True, exist_ok=True)
    SPLIT_PATH.write_text(json.dumps(split, indent=1))
    return split


if __name__ == "__main__":
    s = make_split()
    print(f"{len(s['pages'])} unique pages, {s['duplicates_removed']} duplicate files removed")
    print("dev patients", s["dev_patients"], "| test patients", s["test_patients"])
