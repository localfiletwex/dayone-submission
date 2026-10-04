"""Start the prototype: the simulated central server (port 8001) and the chat app (port 8000).

    python scripts/run_demo.py --fake            # no key: the whole flow with INVENTED values
    python scripts/run_demo.py --key YOUR_KEY    # real reading with Gemini
    python scripts/run_demo.py                   # real reading, key taken from GEMINI_API_KEY

Works the same on Linux, macOS and Windows (no bash needed).  Stop with Ctrl+C.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fake", action="store_true", help="simulated extractor: invented values, no key, no network")
    ap.add_argument("--key", help="Gemini API key (otherwise the GEMINI_API_KEY environment variable)")
    a = ap.parse_args()
    env = dict(os.environ)
    if a.fake:
        env["DAYONE_EXTRACTOR"] = "fake"
    if a.key:
        env["GEMINI_API_KEY"] = a.key

    def serve(app, port):
        return subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
                                cwd=ROOT, env=env)

    procs = [serve("app.sync_server:app", 8001), serve("app.server:app", 8000)]
    print("Open http://127.0.0.1:8000  (accounts: midwife1, midwife2, supervisor1, data1; "
          "the first login sets the device PIN)")
    print("Dashboard of anonymised aggregates: http://127.0.0.1:8001/dashboard   |   Stop with Ctrl+C", flush=True)
    code = 0
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        code = next(p.returncode for p in procs if p.poll() is not None) or 1
        print("One of the two servers stopped (see the message above). If it says \"address already in use\", "
              "an earlier copy is still running: close it and start again.")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(code)


if __name__ == "__main__":
    main()
