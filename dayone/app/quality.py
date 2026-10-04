"""Image-quality check, run ON THE DEVICE when a photo is captured.

It answers one question before a page is sent to the AI: is this photo likely
to be hard to read?  It needs no network and no model: a few statistics of
the grey-level image (Pillow + numpy), computed in a few tens of milliseconds.

The check never refuses a capture and never deletes one: the photo is always
saved first ("no record ever lost").  When it finds a problem, the agent says
which one and offers to retake the photo or to keep it anyway; the midwife
decides.

The four thresholds were set on the provided synthetic data (clean pages, the
five real photos, and the synthetic degradations of eval/degrade.py); see
tests/test_quality.py for the checks and README for the measured results.
They are deliberately conservative: a doubtful photo is let through, because
a wrong warning costs the midwife a retake.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, UnidentifiedImageError

# Issue codes (the chat translates them: i18n "quality.<code>")
BLURRED, DARK, LOW_CONTRAST, SMALL = "blurred", "dark", "low_contrast", "small"

WORK_SIDE = 1000            # the statistics are computed on a copy this many pixels on its long side
MIN_SIDE_PX = 600           # shorter side of the ORIGINAL photo; below this, handwriting is a few pixels high
MIN_PAPER_LEVEL = 110       # median grey level (0-255); below it the photo is too dark
MIN_SHARPNESS = 0.50        # strongest edges relative to the paper level; below it the photo is blurred
MIN_INK_CONTRAST = 0.35     # how much darker the ink is than the paper (0-1); below it the writing is faint


def measure(image: bytes) -> dict | None:
    """Grey-level statistics of a photo, or None when the bytes are not an image."""
    try:
        img = Image.open(io.BytesIO(image))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    width, height = img.size
    grey = img.convert("L")
    grey.thumbnail((WORK_SIDE, WORK_SIDE))
    a = np.asarray(grey, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return {"width": width, "height": height, "paper": float(a.mean()), "sharpness": 0.0, "ink_contrast": 0.0}
    # Paper level: the median pixel (most of a registry page is paper).
    paper = float(np.percentile(a, 50))
    # Sharpness: how strong the strongest edges are (99.5th percentile of the Laplacian),
    # relative to the paper level so that a dim but sharp photo is not called blurred.
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    sharp = float(np.percentile(np.abs(lap), 99.5)) / max(paper, 1.0)
    # Ink contrast: the darkest half-percent of the pixels (ink) against the paper.
    ink = float(np.percentile(a, 0.5))
    return {"width": width, "height": height, "paper": round(paper, 1), "sharpness": round(sharp, 3),
            "ink_contrast": round(max(paper - ink, 0.0) / max(paper, 1.0), 3)}


def assess(image: bytes) -> dict | None:
    """Return {"ok": bool, "issues": [codes], "metrics": {...}}, or None when the
    bytes cannot be decoded as an image (the check is then skipped, not failed)."""
    m = measure(image)
    if m is None:
        return None
    issues = []
    if min(m["width"], m["height"]) < MIN_SIDE_PX:
        issues.append(SMALL)
    if m["paper"] < MIN_PAPER_LEVEL:
        issues.append(DARK)
    if m["sharpness"] < MIN_SHARPNESS:
        issues.append(BLURRED)
    if m["ink_contrast"] < MIN_INK_CONTRAST:
        issues.append(LOW_CONTRAST)
    return {"ok": not issues, "issues": issues, "metrics": m}
