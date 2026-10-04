#!/usr/bin/env bash
# One command to run the whole evaluation with your own Gemini key:
#     GEMINI_API_KEY=... bash scripts/run_eval.sh [--clean-only]
# The work is done by scripts/run_eval.py (see its header for the options), which also runs
# without bash (Windows).
cd "$(dirname "$0")/.."
exec python scripts/run_eval.py "$@"
