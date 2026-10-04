"""One command to check that this copy of the prototype is in working order.

    python scripts/check.py                  # tests + start-up check, no key needed
    python scripts/check.py --key YOUR_KEY   # the same, plus ONE live call to the model
                                             # (a key in GEMINI_API_KEY is used too)

It changes nothing: it uses a temporary device folder and its own ports.
Works the same on Linux, macOS and Windows.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP, SYNC = "http://127.0.0.1:8010", "http://127.0.0.1:8011"


def step(title):
    print(f"\n== {title}", flush=True)


def call(url, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    return urllib.request.urlopen(req, timeout=5).read()


def startup_check() -> bool:
    tmp = Path(tempfile.mkdtemp())
    env = {**os.environ, "DAYONE_SERVER_DB": str(tmp / "server.db"), "DAYONE_DEVICE_DIR": str(tmp / "device"),
           "DAYONE_EXTRACTOR": "fake", "DAYONE_SYNC_URL": SYNC}
    env.pop("DAYONE_PIN", None)

    def serve(app, port, log):
        return subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "error"],
                                cwd=ROOT, env=env, stdout=open(tmp / log, "w"), stderr=subprocess.STDOUT)

    procs = [serve("app.sync_server:app", 8011, "sync.log"), serve("app.server:app", 8010, "app.log")]
    ok = True

    def check(what, cond):
        nonlocal ok
        print(("   ok      " if cond else "   FAILED  ") + what)
        ok = ok and bool(cond)

    try:
        for _ in range(80):
            try:
                call(APP + "/"), call(SYNC + "/sync/records")
                break
            except OSError:
                time.sleep(0.25)
        else:
            print("   FAILED  the servers did not start:")
            for log in ("app.log", "sync.log"):
                print("   " + (tmp / log).read_text(errors="replace")[-600:].replace("\n", "\n   "))
            return False
        check("chat page", b"Registre maternel" in call(APP + "/"))
        check("dashboard page", b"Tableau de bord" in call(SYNC + "/dashboard"))
        check("dashboard data", "registries" in json.loads(call(SYNC + "/dashboard/data")))
        login = json.loads(call(APP + "/api/login", {"username": "midwife1", "pin": "0000"}))
        check("login on a fresh device", "token" in login)
        check("French interface", json.loads(call(APP + "/api/language", {"lang": "fr"}, login["token"])) == {"lang": "fr"})
        reply = json.loads(call(APP + "/api/message", {"text": "aide"}, login["token"]))
        check("the agent answers in French", "Envoyez-moi une photo" in reply[-1]["text"])
        state = json.loads(call(APP + "/api/state", token=login["token"]))
        check("status information present", all(k in state for k in ("ai_why", "model", "dashboard_url", "lang")))
    except Exception as e:                                   # noqa: BLE001 - report anything, cleanly
        check(f"start-up check ({type(e).__name__}: {e})", False)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--key", help="Gemini API key for the live call (otherwise GEMINI_API_KEY, otherwise skipped)")
    a = ap.parse_args()
    if a.key:
        os.environ["GEMINI_API_KEY"] = a.key
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    failed = False

    step("1/4 automated tests")
    run = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True)
    print("\n".join((run.stdout + run.stderr).strip().splitlines()[-3:]))
    failed |= run.returncode != 0

    step("2/4 the app and the simulated server start, and their pages load")
    failed |= not startup_check()

    step("3/4 settings this shell would give the app")
    from app import config
    t = config.load_threshold()
    print(f"   model: {config.GEMINI_MODEL}   thinking level: {config.GEMINI_THINKING_LEVEL}   extractor: {config.EXTRACTOR}")
    print(f"   follow-up threshold: {t['value']} "
          f"({'from the evaluation, model ' + str(t.get('model')) if t['calibrated'] else 'provisional, not calibrated'})")
    if t.get("arabic"):
        print(f"   pages with Arabic entries: {'numbers' if t['arabic_scope'] == 'numbers' else 'every filled field'} "
              f"below {t['arabic']} are asked about")
    if t.get("model") and t["model"] != config.GEMINI_MODEL:
        print("   WARNING: the threshold was calibrated with a different model than the one configured.")
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    print("   API key: " + ("set" if has_key else "NOT set (the app would say so and keep pages pending)"))

    step("4/4 one live call to the model")
    if has_key:
        failed |= subprocess.run([sys.executable, "-m", "eval.evaluate", "--smoke"]).returncode != 0
    else:
        print("   skipped: no key given")

    print("\n" + ("SOME CHECKS FAILED - see above" if failed else "ALL CHECKS PASSED"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
