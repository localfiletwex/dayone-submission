"""The command-line scripts start, explain themselves, and refuse cleanly."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, env=None):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, env=env)


def test_scripts_explain_themselves_and_need_no_bash():
    for name in ("run_demo.py", "check.py", "run_eval.py", "pack_submission.py"):
        r = run(f"scripts/{name}", "--help")
        assert r.returncode == 0 and "usage:" in r.stdout, name
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
    r = run("scripts/run_eval.py", "--clean-only", env=env)                  # no key: a clear message, no traceback
    assert r.returncode != 0 and "Give a Gemini API key" in r.stderr and "eval.replay" in r.stderr
    assert "Traceback" not in r.stderr
    for wrapper in ("run_demo.sh", "check.sh", "run_eval.sh", "run_eval_languages.sh"):
        assert "exec python scripts/" in (ROOT / "scripts" / wrapper).read_text()


def test_submission_zip_leaves_out_private_and_rebuildable_files_and_spots_a_key(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import pack_submission as P
    proj = tmp_path / "dayone"
    for rel in ("app/server.py", "README.md", "device_data/dayone.db", ".venv/x.py", "work/fonts/a.ttf",
                "work/results/eval.md", "work/languages/ar/page-01.png", "data/data/Paper Registry/p.png",
                "app/__pycache__/server.cpython-312.pyc", "notes.txt"):
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text("x")
    monkeypatch.setattr(P, "ROOT", proj)
    names = {n for _, n in P.files(without_dataset=False, without_languages=False)}
    assert names == {"dayone/app/server.py", "dayone/README.md", "dayone/work/results/eval.md", "dayone/notes.txt",
                     "dayone/work/languages/ar/page-01.png", "dayone/data/data/Paper Registry/p.png"}
    small = {n for _, n in P.files(without_dataset=True, without_languages=True)}
    assert "dayone/data/data/Paper Registry/p.png" not in small and "dayone/work/languages/ar/page-01.png" not in small
    (proj / "notes.txt").write_text("key = AIza" + "B" * 35)
    out = tmp_path / "s.zip"
    with zipfile.ZipFile(out, "w") as z:
        for path, name in P.files(False, False):
            z.write(path, name)
    assert P.verify(out, with_dataset=False, with_languages=False) is False      # incomplete, and a key inside
