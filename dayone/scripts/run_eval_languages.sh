#!/usr/bin/env bash
# Measure the reading of ENGLISH and ARABIC entries, on the synthetic pages made by eval/make_languages.py:
#     GEMINI_API_KEY=... bash scripts/run_eval_languages.sh          # test patients: 64 pages, about 130 calls
#     GEMINI_API_KEY=... bash scripts/run_eval_languages.sh all      # dev and test patients, about 320 calls
# The work is done by scripts/run_eval.py --languages, which also runs without bash (Windows).
cd "$(dirname "$0")/.."
if [ "${1:-}" = "all" ]; then exec python scripts/run_eval.py --languages all; else exec python scripts/run_eval.py --languages; fi
