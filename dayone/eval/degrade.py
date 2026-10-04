"""Write SYNTHETICALLY degraded copies of the page images to work/degraded/.
Originals are only read.  Fixed seed => reproducible.

Each unique page gets one degradation, assigned round-robin after a seeded
shuffle, so the four types are balanced (use --all for every type per page).

    python -m eval.degrade [--all]
"""
from __future__ import annotations

import io
import json
import random
import sys

import numpy as np
from PIL import Image, ImageFilter

from eval.paths import DEGRADED_DIR
from eval.split import unique_pages

SEED = 2026
TYPES = ("blur", "shadow", "skew", "lowlight")


def _jpeg(img, q):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def degrade(img: Image.Image, kind: str, rng: random.Random) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    if kind == "blur":
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(1.6, 2.6)))
    elif kind == "shadow":
        a = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:h, 0:w]
        ang = rng.uniform(0, np.pi)
        ramp = (np.cos(ang) * xx / w + np.sin(ang) * yy / h)
        ramp = (ramp - ramp.min()) / (ramp.max() - ramp.min())
        mask = 1.0 - rng.uniform(0.45, 0.65) * np.clip((ramp - 0.35) * 2.2, 0, 1)
        img = Image.fromarray(np.clip(a * mask[..., None], 0, 255).astype(np.uint8))
    elif kind == "skew":
        img = img.rotate(rng.uniform(-5, 5), resample=Image.BICUBIC, expand=True, fillcolor=(40, 40, 44))
        w2, h2 = img.size
        d = rng.uniform(0.03, 0.06)
        quad = (w2 * d, 0, 0, h2, w2, h2 * (1 - d / 2), w2 * (1 - d), h2 * d / 2)
        img = img.transform((w2, h2), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=(40, 40, 44))
    elif kind == "lowlight":
        a = np.asarray(img).astype(np.float32) * rng.uniform(0.32, 0.45)
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 9, a.shape)
        img = Image.fromarray(np.clip(a + noise, 0, 255).astype(np.uint8))
    else:
        raise ValueError(kind)
    img.thumbnail((1280, 1810))                      # phone-like resolution
    return _jpeg(img, rng.randint(45, 60))


def main(all_types=False):
    pages, _ = unique_pages()
    rng = random.Random(SEED)
    order = sorted(pages)
    rng.shuffle(order)
    DEGRADED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for i, n in enumerate(order):
        kinds = TYPES if all_types else (TYPES[i % len(TYPES)],)
        src = Image.open(pages[n])
        for kind in kinds:
            out = DEGRADED_DIR / f"page-{n:02d}-{kind}.jpg"
            degrade(src, kind, random.Random(f"{SEED}-{n}-{kind}")).save(out, "JPEG", quality=92)
            manifest[out.name] = {"page": n, "degradation": kind, "synthetic": True}
    (DEGRADED_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(f"wrote {len(manifest)} synthetic degraded images to {DEGRADED_DIR}")


if __name__ == "__main__":
    main("--all" in sys.argv)
