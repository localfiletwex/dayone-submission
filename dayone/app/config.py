"""Runtime configuration (environment variables, paths, thresholds)."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVICE_DIR = Path(os.environ.get("DAYONE_DEVICE_DIR", ROOT / "device_data"))
THRESHOLD_FILE = Path(os.environ.get("DAYONE_THRESHOLD_FILE", ROOT / "config" / "threshold.json"))
SYNC_URL = os.environ.get("DAYONE_SYNC_URL", "http://127.0.0.1:8001")
EXTRACTOR = os.environ.get("DAYONE_EXTRACTOR", "gemini")       # gemini | fake | disabled
# The ONE place the model is chosen.  Change it here or with the environment variables.
# Default = the model the evaluation in work/results/ was run with and the follow-up
# threshold was calibrated on (gemini-3.8-flash was the first choice but was overloaded).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Thinking level sent to the model.  Start with "low"; a variable to compare later
# ("low" vs "high").  Set to "none" to send no thinking configuration at all.
GEMINI_THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "low")
# Tell the model that entries may be in French, Arabic or English (prompt "v2").  OFF by default:
# measured on the dev patients with gemini-3.5-flash-lite, the added rules lowered the accuracy on
# FRENCH entries from 98.3% to 90.0% (one run each, see README "Results").  Without them the prompt
# is the "v1" the threshold was calibrated with.  The code that TYPES Arabic or English entries
# (app/languages.py) is always active; this switch only changes what the model is told.
PROMPT_LANGUAGE_RULES = os.environ.get("DAYONE_PROMPT_LANGUAGES", "0") == "1"
MAX_PROCESSING_ATTEMPTS = 3
MAX_SYNC_ATTEMPTS = 5
# When the AI service itself is in trouble (overloaded, quota, refused key), the worker waits
# this long before asking again, doubling up to the maximum.  Sync keeps running meanwhile.
AI_RETRY_FIRST_SECONDS = 10
AI_RETRY_MAX_SECONDS = 60

# The follow-up threshold must come from evaluation data (eval/evaluate.py
# writes config/threshold.json).  Until that has been run, this provisional
# value is used and reported as such everywhere.
PROVISIONAL_THRESHOLD = 0.85


def load_threshold() -> dict:
    """Return {"value": float, "calibrated": bool, "source": str, "model": str | None, "arabic": float | None}.

    "model" is the model the threshold was calibrated with (None when it was
    not calibrated): the app warns when it runs with a different one.

    "arabic" is an optional, stricter threshold for pages that have entries in
    Arabic script (None: the same threshold as everything else), applied to
    their numbers ("arabic_scope": "numbers") or to every filled field ("all").
    Measured on the synthetic Arabic pages, the model misreads numbers written
    with Arabic-script digits (2 and 3 above all) and gives them a confidence
    of about 0.90, far above the general threshold, so they would be stored
    without a question.  It comes from DAYONE_THRESHOLD_ARABIC, or from
    "threshold_arabic" in the threshold file (python -m eval.evaluate --set-arabic-threshold).
    """
    d = json.loads(THRESHOLD_FILE.read_text()) if THRESHOLD_FILE.exists() else {}
    arabic = os.environ.get("DAYONE_THRESHOLD_ARABIC") or d.get("threshold_arabic")
    extra = {"arabic": float(arabic) if arabic else None,
             "arabic_scope": "all" if d.get("threshold_arabic_scope") == "all" else "numbers"}
    env = os.environ.get("DAYONE_THRESHOLD")
    if env:
        return {"value": float(env), "calibrated": False, "source": "DAYONE_THRESHOLD env", **extra}
    if d:
        return {"value": float(d["threshold"]), "calibrated": True, "source": str(THRESHOLD_FILE),
                "model": d.get("model"), **extra}
    return {"value": PROVISIONAL_THRESHOLD, "calibrated": False, "source": "provisional default (not calibrated)", **extra}
