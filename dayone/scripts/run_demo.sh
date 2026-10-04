#!/usr/bin/env bash
# Starts the simulated sync server (port 8001) and the chat app (port 8000).
#     GEMINI_API_KEY=... bash scripts/run_demo.sh          # real extraction
#     DAYONE_EXTRACTOR=fake bash scripts/run_demo.sh       # no key: canned, invented values
# The work is done by scripts/run_demo.py, which also runs without bash (Windows).
cd "$(dirname "$0")/.."
exec python scripts/run_demo.py "$@"
