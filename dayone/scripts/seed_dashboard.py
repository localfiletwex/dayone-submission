"""Fill the simulated server with the ten SYNTHETIC patients of the dataset, so
that the dashboard has something to show in a demo.

    python -m eval.build_ground_truth          # once, if work/ground_truth/gt.json does not exist
    python scripts/seed_dashboard.py           # the sync server must be running (scripts/run_demo.sh)

The values are the dataset's own reference values (read from the registry PDF
by eval/build_ground_truth.py), not something the AI extracted: this script
only demonstrates the dashboard, it says nothing about extraction quality.
Nothing in data/ is modified.  Seeded records have ids starting with "seed-";
delete server_data/ to remove them.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
GT = ROOT / "work" / "ground_truth" / "gt.json"
URL = os.environ.get("DAYONE_SYNC_URL", "http://127.0.0.1:8001").rstrip("/") + "/sync/records"


def _value(cell: dict) -> dict:
    return {"value": cell.get("value"), "status": cell["status"], "confidence": 1.0, "source": "seed", "note": ""}


def payloads() -> list[dict]:
    if not GT.exists():
        sys.exit("work/ground_truth/gt.json not found: run  python -m eval.build_ground_truth  first.")
    by_patient: dict[int, dict] = {}
    for page in json.loads(GT.read_text(encoding="utf-8")).values():
        section = {"fields": {k: _value(v) for k, v in page["fields"].items()},
                   "groups": {g: [{"slot": it["slot"], "fields": {k: _value(v) for k, v in it["fields"].items()}}
                                  for it in items] for g, items in page.get("groups", {}).items()}}
        by_patient.setdefault(page["patient"], {})[page["page_type"]] = section
    return [{"record_id": f"seed-registry-{n:02d}", "version": 1, "patient_id": f"seed-patient-{n:02d}",
             "midwife_id": "seed", "supersedes": None, "sections": sections, "pages": []}
            for n, sections in sorted(by_patient.items())]


def main():
    sent = 0
    for p in payloads():
        req = urllib.request.Request(URL, data=json.dumps(p).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                sent += json.loads(r.read()).get("result") == "stored"
        except OSError as e:
            sys.exit(f"Could not reach the sync server at {URL} ({type(e).__name__}). Start it with scripts/run_demo.sh.")
    print(f"{sent} synthetic registries added (already present ones are not duplicated). "
          f"Open {URL.rsplit('/sync', 1)[0]}/dashboard")


if __name__ == "__main__":
    main()
