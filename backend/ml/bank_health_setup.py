"""
Phase 3 -- populate `bank_health_observations` (Decision B2).

db/db.py's DDL has carried this table, structurally empty, since Phase 1,
documented there as "consumed from Phase 3 onward." Phase 2's Data Factory
deliberately never wrote to it (its own health series lives only in its CSV
export / in-memory HealthIndex -- see backend/phase2_notes.md, "Deliberately
NOT done"). This script is what Phase 3 does with that reserved extension
point: it does NOT reopen or modify Phase 2 -- it re-derives the exact same
baseline-seed-42 health series Phase 2's generator already produces
(bank_health_timeseries.generate_bank_health_timeseries is deterministic
given (seed, profile, n_merchants, n_customers, horizon_hours), proven
reproducible by Phase 2's own reproducibility_check) and loads it into the
production table, so training and live inference can both read network
health from ONE shared source (see ml/outcome_features.py).

Units note: window_start/window_end are stored in SIMULATED HOURS (the same
unit every other Data Factory timestamp uses -- sim_hour), not epoch
seconds. This is a synthetic/demo system end-to-end (SoT Section 6); a real
production integration would need a units adapter at the ingestion boundary,
which is out of scope here.

Run from the directory containing backend/:
    python -m backend.ml.bank_health_setup
"""

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import numpy as np

from backend.data_factory import entities as ent
from backend.data_factory import bank_health_timeseries as bht
from backend.data_factory import calibration_profiles as cp
from backend.data_factory import candidate_outcome_dataset as cod
from backend.db import db as db_module

SEED = 42  # the Phase 3 primary/training seed (Decision F)
PROFILE_NAME = "baseline"  # the model is trained ONLY on the baseline profile


def regenerate_health_observations(seed=SEED, profile_name=PROFILE_NAME):
    """
    Re-derives the world + health series exactly as
    candidate_outcome_dataset.generate_dataset()'s first few lines do, but
    without paying for case generation (health/world construction consumes
    world_rng before case_rng is even created, so it is independent of
    n_cases -- calling this instead of generate_dataset(..., n_cases=N) is a
    cheaper path to the identical result, not a different one).
    """
    profile = cp.get_profile(profile_name)
    world_rng = np.random.default_rng(seed)
    world = ent.SyntheticWorld(world_rng, profile,
                                n_merchants=cod.DEFAULT_N_MERCHANTS,
                                n_customers=cod.DEFAULT_N_CUSTOMERS)
    health_obs = bht.generate_bank_health_timeseries(
        world_rng, world, cod.DEFAULT_HORIZON_HOURS, profile)
    return health_obs


def load_into_db(health_obs, conn=None, seed=SEED, profile_name=PROFILE_NAME):
    """Wipes any prior rows tagged with this (seed, profile) and re-inserts.
    Idempotent: re-running produces the same row count and values (the
    generator is deterministic)."""
    owns_conn = conn is None
    if owns_conn:
        conn = db_module.get_connection()
    try:
        conn.execute("DELETE FROM bank_health_observations")
        conn.executemany(
            "INSERT INTO bank_health_observations "
            "(bank, method, psp, window_start, window_end, success_rate, timeout_rate, health_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(o.bank, o.method, o.psp, o.window_start, o.window_end,
              o.success_rate, o.timeout_rate, o.health_score) for o in health_obs],
        )
        # Index for the rolling-window lookup (bank, method, psp, window_start) --
        # additive, does not touch db.py's DDL.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_health_lookup "
            "ON bank_health_observations(bank, method, psp, window_start)"
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


def main():
    t0 = time.time()
    health_obs = regenerate_health_observations()
    load_into_db(health_obs)
    conn = db_module.get_connection()
    n = conn.execute("SELECT COUNT(*) FROM bank_health_observations").fetchone()[0]
    conn.close()
    print(f"bank_health_observations populated: {n} rows "
          f"(seed={SEED}, profile={PROFILE_NAME}, {time.time() - t0:.1f}s)")
    assert n == len(health_obs), f"row count mismatch: DB has {n}, generated {len(health_obs)}"
    return 0


if __name__ == "__main__":
    sys.exit(main())
