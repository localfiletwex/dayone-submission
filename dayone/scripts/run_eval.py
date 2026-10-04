"""The whole evaluation in one command, with your own Gemini key.

    python scripts/run_eval.py --key YOUR_KEY [--clean-only]
    python scripts/run_eval.py --languages [all] --key YOUR_KEY     # the synthetic English and Arabic pages

(The key may also be in the GEMINI_API_KEY environment variable.)

  --clean-only            the clean renders only (skips the synthetic degraded copies)
  --languages             measure the English and Arabic pages of eval/make_languages.py instead:
                          test patients (64 pages, about 130 calls), or with "all" dev and test (about 320)
  GEMINI_MODEL            model id            (default: app/config.py)
  GEMINI_THINKING_LEVEL   thinking level      (default: low)
  GEMINI_MIN_INTERVAL     seconds between calls, for low rate limits
  DAYONE_PROMPT_LANGUAGES=1   measure the prompt that tells the model about the three languages (v2)

Answers are cached per model, thinking level and prompt under work/extraction_cache, so an
interrupted run resumes where it stopped.  The script stops at the first problem.
Works the same on Linux, macOS and Windows.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*module_args, quiet=False):
    """python -m <module> <args>; stop the whole script if it fails."""
    r = subprocess.run([sys.executable, "-m", *module_args], cwd=ROOT,
                       stdout=subprocess.DEVNULL if quiet else None)
    if r.returncode != 0:
        sys.exit(r.returncode)


def french(extra):
    print("== 2/6 ground truth, split, synthetic degradations", flush=True)
    run("eval.build_ground_truth"), run("eval.split"), run("eval.degrade")
    print("== 3/6 dev patients: accuracy, calibration, threshold (stops if 2% needs >25% of filled fields asked)", flush=True)
    run("eval.evaluate", "--split", "dev", "--write-threshold", *extra)
    print("== 4/6 test patients: threshold from dev is only REPORTED here", flush=True)
    run("eval.evaluate", "--split", "test", *extra)
    print("== 5/6 the 5 real photos (qualitative, no ground truth)", flush=True)
    run("eval.evaluate", "--photos")
    print("== 6/6 done")
    print("Results: work/results/ (eval-*.md, curve-*.csv, real-photos-qualitative.json) and config/threshold.json")


def languages(splits):
    print("== 2/3 the English and Arabic pages", flush=True)
    gt = ROOT / "work" / "ground_truth"
    if (gt / "gt-en.json").exists() and (gt / "gt-ar.json").exists() and (ROOT / "work" / "languages" / "ar").is_dir():
        print("found in work/languages/")
    else:
        run("eval.build_ground_truth", quiet=True), run("eval.split", quiet=True), run("eval.make_languages")
    if not (ROOT / "work" / "split.json").exists():
        run("eval.split", quiet=True)
    print("== 3/3 evaluation", flush=True)
    for lang in ("en", "ar"):
        for split in splits:
            print(f"-- {lang}, {split} patients", flush=True)
            run("eval.evaluate", "--language", lang, "--split", split)
    print("Results: work/results/eval-*-en-prompt-*.md and eval-*-ar-prompt-*.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--key", help="Gemini API key (otherwise the GEMINI_API_KEY environment variable)")
    ap.add_argument("--clean-only", action="store_true")
    ap.add_argument("--languages", nargs="?", const="test", choices=("test", "all"))
    a = ap.parse_args()
    if a.key:
        os.environ["GEMINI_API_KEY"] = a.key
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("Give a Gemini API key: --key YOUR_KEY (or set GEMINI_API_KEY).\n"
                 "To check the saved results WITHOUT a key:  python -m eval.replay")
    steps = "3" if a.languages else "6"
    print(f"== 1/{steps} smoke test: one call to check the key, the model id and the thinking level", flush=True)
    run("eval.evaluate", "--smoke")
    if a.languages:
        languages(("dev", "test") if a.languages == "all" else ("test",))
    else:
        french(["--clean-only"] if a.clean_only else [])


if __name__ == "__main__":
    main()
