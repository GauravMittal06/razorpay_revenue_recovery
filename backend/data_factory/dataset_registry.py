"""
Data Factory -- dataset registry. Phase 2.

Every generation run gets one manifest recording dataset name, version,
seed, calibration profile, generator version, row/case counts, and
validator results -- per execution plan Section 5/6/9's "every dataset
generation run recorded ... before its output is used to train or
evaluate anything."

Two write targets, both populated by register_run():
  1. A local JSON manifest file under data_factory/registry/ -- always
     written, has zero coupling to the production DB, and is what
     validators.reproducibility_check reads back to compare two runs.
  2. Optionally, a row in the production `dataset_registry` SQLite table
     (backend/db/db.py already defines this table, structurally present
     since Phase 1, explicitly documented there as "populated starting
     Phase 2"). This is a metadata/manifest table, not production
     business data (opportunities/payments/customers) -- writing a
     generation-run manifest to it does not violate the Data Factory's
     "never touches production data" boundary in Section 6, since no
     opportunity, payment, or customer row is ever read or written here.
     Controlled by an explicit `db_path` argument; omitted entirely by
     default so pure offline generation never requires a DB at all.
"""

import json
import sqlite3
import time
from pathlib import Path

REGISTRY_DIR = Path(__file__).resolve().parent / "registry"


def _manifest_path(dataset_name, version, seed, profile_name):
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{dataset_name}__v{version}__seed{seed}__{profile_name}.json"
    return REGISTRY_DIR / fname


def register_run(dataset_name, version, seed, calibration_profile_name,
                  generator_version, row_count, case_count, validator_results: dict,
                  db_path=None, extra: dict = None):
    """
    Writes the JSON manifest (always) and, if db_path is given, a row in
    the production dataset_registry table. Returns the manifest dict.
    """
    manifest = {
        "dataset_name": dataset_name,
        "version": version,
        "seed": seed,
        "calibration_profile": calibration_profile_name,
        "generator_version": generator_version,
        "row_count": row_count,
        "case_count": case_count,
        "validator_results": validator_results,
        "created_at": int(time.time()),
    }
    if extra:
        manifest["extra"] = extra

    path = _manifest_path(dataset_name, version, seed, calibration_profile_name)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    manifest["_manifest_path"] = str(path)

    if db_path is not None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO dataset_registry
                (dataset_name, version, seed, calibration_profile, generator_version,
                 row_count, case_count, validator_results, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_name, version, seed, calibration_profile_name, generator_version,
                    row_count, case_count, json.dumps(validator_results), manifest["created_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return manifest


def load_manifest(dataset_name, version, seed, profile_name):
    path = _manifest_path(dataset_name, version, seed, profile_name)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def list_registered_runs():
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(REGISTRY_DIR.glob("*.json")):
        with open(p) as f:
            out.append(json.load(f))
    return out
