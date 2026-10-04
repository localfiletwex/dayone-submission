#!/usr/bin/env bash
# One command to check that this copy of the prototype is in working order:
#     bash scripts/check.sh                      # tests + start-up check
#     GEMINI_API_KEY=... bash scripts/check.sh   # the same, plus ONE live call to the model
# The work is done by scripts/check.py, which also runs without bash (Windows).
cd "$(dirname "$0")/.."
exec python scripts/check.py "$@"
