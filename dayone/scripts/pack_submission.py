"""Pack this folder into ONE self-contained zip for the organisers, and prove that it works.

    python scripts/pack_submission.py                       # writes ../dayone-submission.zip
    python scripts/pack_submission.py --out /path/to/file.zip
    python scripts/pack_submission.py --without-languages   # smaller: leaves out the English / Arabic pages
    python scripts/pack_submission.py --without-dataset     # leaves out ./data (the organisers' own dataset)

What goes in: the code, tests and documentation; the calibrated threshold (config/); the
evaluation results and the model's saved answers (work/), so that the numbers can be checked
without a Gemini key; the reference values; the English and Arabic test pages; and the
organisers' dataset as it was received (./data), so that nothing has to be fetched or placed
by hand.

What stays out: the Python environment, the encrypted device and server data of your own
test sessions, caches, and anything that can be rebuilt (degraded copies, fonts).

After writing the zip the script checks it: the files a reviewer needs are inside, no file
contains something that looks like an API key, and - unless --no-test - the zip is unpacked
in a temporary folder where the tests are run and the French test-patient results are
reproduced from the saved answers, without a key and without the network.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "venv", "device_data", "server_data", "__pycache__", ".pytest_cache", ".git",
             "backup_before_languages", "fonts", "degraded", "node_modules"}
SKIP_FILES = {"languages_update_tests.txt", ".DS_Store"}
SKIP_SUFFIXES = (".before_login_fix", ".pyc", ".db", ".db-shm", ".db-wal")
KEY_PATTERN = re.compile(rb"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{30,}")
TEXT_SUFFIXES = (".py", ".md", ".sh", ".txt", ".json", ".html", ".csv", ".cfg", ".toml", ".yml", ".yaml", ".env", "")
SIZE_WARNING_MB = 35


def files(without_dataset: bool, without_languages: bool):
    for root, dirs, names in os.walk(ROOT):
        rel_root = Path(root).relative_to(ROOT)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not (without_dataset and rel_root == Path(".") and d == "data")
                         and not (without_languages and rel_root == Path("work") and d == "languages"))
        for name in sorted(names):
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES):
                continue
            if without_languages and rel_root == Path("work/ground_truth") and name in ("gt-en.json", "gt-ar.json"):
                continue
            yield Path(root) / name, (Path(ROOT.name) / rel_root / name).as_posix()


def verify(zip_path: Path, with_dataset: bool, with_languages: bool) -> bool:
    top = ROOT.name
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        need = {
            "code and documentation": [f"{top}/README.md", f"{top}/requirements.txt", f"{top}/app/server.py",
                                       f"{top}/scripts/check.py", f"{top}/CRITERIA.md"],
            "calibrated threshold": [f"{top}/config/threshold.json"],
            "reference values and patient split": [f"{top}/work/ground_truth/gt.json", f"{top}/work/split.json"],
        }
        ok = True
        for what, wanted in need.items():
            missing = [w for w in wanted if w not in names]
            print(("   ok      " if not missing else "   MISSING ") + what + (": " + ", ".join(missing) if missing else ""))
            ok = ok and not missing
        counts = {
            "evaluation reports (work/results/*.md)": (sum(n.startswith(f"{top}/work/results/") and n.endswith(".md") for n in names), 2),
            "saved model answers (work/extraction_cache)": (sum(n.startswith(f"{top}/work/extraction_cache/") for n in names), 80),
        }
        if with_dataset:
            counts["dataset pages (data/data/Paper Registry)"] = (sum(n.startswith(f"{top}/data/data/Paper Registry/") for n in names), 80)
        if with_languages:
            counts["English and Arabic pages (work/languages)"] = (sum(n.startswith(f"{top}/work/languages/") for n in names), 160)
        for what, (n, at_least) in counts.items():
            good = n >= at_least
            print(("   ok      " if good else "   MISSING ") + f"{what}: {n} file(s)" + ("" if good else f", expected at least {at_least}"))
            ok = ok and good
        leaks = [n for n in names if n.lower().endswith(TEXT_SUFFIXES) and KEY_PATTERN.search(z.read(n))]
        print(("   ok      no file contains something that looks like an API key" if not leaks
               else "   FAILED  these files contain what looks like an API key: " + ", ".join(leaks)))
        return ok and not leaks


def self_test(zip_path: Path, with_dataset: bool) -> bool:
    tmp = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        folder = tmp / ROOT.name
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GEMINI_", "GOOGLE_API", "DAYONE_", "WHATSAPP_"))}
        env["GEMINI_API_BASE"] = "http://127.0.0.1:9"            # nothing may reach the model during the test
        r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                           cwd=folder, env=env, capture_output=True, text=True)
        line = (r.stdout + r.stderr).strip().splitlines()[-1]
        print(("   ok      " if r.returncode == 0 else "   FAILED  ") + "tests in the unpacked zip: " + line)
        ok = r.returncode == 0
        if not with_dataset:
            print("   skipped the reproduction of the results: the dataset is not in the zip")
            return ok
        r = subprocess.run([sys.executable, "-m", "eval.evaluate", "--split", "test", "--clean-only"],
                           cwd=folder, env=env, capture_output=True, text=True)
        out = r.stdout + r.stderr
        calls = re.search(r"API calls: (\d+) \| cache hits: (\d+)", out)
        acc = re.search(r"filled-field accuracy ([\d.]+%)", out)
        good = r.returncode == 0 and calls and calls.group(1) == "0" and acc
        print(("   ok      " if good else "   FAILED  ") + "French test patients reproduced without a key: "
              + (f"{acc.group(1)} of filled fields, {calls.group(2)} pages from the saved answers, {calls.group(1)} model calls"
                 if good else out.strip().splitlines()[-1] if out.strip() else "no output"))
        return ok and bool(good)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT.parent / "dayone-submission.zip")
    ap.add_argument("--without-dataset", action="store_true")
    ap.add_argument("--without-languages", action="store_true")
    ap.add_argument("--no-test", action="store_true", help="do not unpack and test the zip")
    a = ap.parse_args()
    with_dataset = not a.without_dataset and (ROOT / "data" / "data" / "Paper Registry").is_dir()
    if not a.without_dataset and not with_dataset:
        print("NOTE: ./data/data/Paper Registry not found, so the dataset cannot be included.")
    with_languages = not a.without_languages and (ROOT / "work" / "languages").is_dir()

    print(f"== 1/3 writing {a.out}", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, name in files(not with_dataset, not with_languages):
            z.write(path, name)
            n += 1
    size = a.out.stat().st_size / 1e6
    print(f"   {n} files, {size:.1f} MB")

    print("== 2/3 what is inside", flush=True)
    ok = verify(a.out, with_dataset, with_languages)

    print("== 3/3 unpacking the zip in a temporary folder and using it", flush=True)
    if a.no_test:
        print("   skipped (--no-test)")
    else:
        ok = self_test(a.out, with_dataset) and ok

    print()
    if size > SIZE_WARNING_MB:
        print(f"NOTE: {size:.0f} MB. Some upload forms accept at most about {SIZE_WARNING_MB} MB; if yours refuses the file, "
              "run again with --without-languages (the French results stay reproducible), or share it by a link.")
    print(f"READY: {a.out}" if ok else "NOT READY - see the lines marked MISSING or FAILED above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
