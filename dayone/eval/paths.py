"""Filesystem locations used by the evaluation tooling.
DATA is read-only input; everything we generate goes under WORK."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DAYONE_DATA", ROOT / "data"))
REGISTRY_DIR = DATA / "data" / "Paper Registry"
REGISTRY_PDF = REGISTRY_DIR / "dossiers_specimen_10_patientes.pdf"
WORK = Path(os.environ.get("DAYONE_WORK", ROOT / "work"))
GT_PATH = WORK / "ground_truth" / "gt.json"
DEGRADED_DIR = WORK / "degraded"
CACHE_DIR = WORK / "extraction_cache"
RESULTS_DIR = WORK / "results"
