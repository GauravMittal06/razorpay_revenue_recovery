"""
Phase 3 -- the ONE feature-construction module. Both ml/train_outcome_model.py
(offline) and ml/inference.py (offline harness AND, later, Phase 4's live
optimizer) import this and call the exact same functions -- this is the whole
train/serve parity mechanism, structurally: there is nowhere else feature
logic is allowed to live.

Contract:
  - `context` describes the opportunity/case: event_type, root_cause, amount,
    current_method, retry_count, days_since_event, days_overdue,
    last_action_type, hours_since_last_action, prior_contacts_in_window,
    merchant_cohort, payment_history_score, past_recovery_rate,
    preferred_channel, bank, psp, decision_time_hours.
  - `candidate` is exactly the shape data_factory.candidate_generation.
    generate_candidates() emits: action_type, timing, timing_hours, method,
    method_changed (optional, default False), channel. This is deliberate --
    it is the shape Phase 4's live optimizer will already have in hand, so
    inference.py never needs a second candidate-shape adapter.
  - Network health is a ROLLING trailing-window aggregate read from
    `bank_health_observations` (Decision B2), computed by the SAME function
    offline and live -- see network_health_rolling() below.

Excluded from every feature frame, always: recovered, recovered_amount,
time_to_recovery_hours (outcomes), every hidden_*/analytic_* column (ground
truth, gate-only), case_id/customer_id/merchant_id/bank/psp (identity --
'bank'/'psp' feed network_health_rolling() but are never one-hot features
themselves), calibration_profile/generator_version (provenance), sim_hour
itself (only decision_time_hours, derived from it, is a feature).
"""

from pathlib import Path

import numpy as np
import pandas as pd


def read_joint_csv(path) -> pd.DataFrame:
    """The ONLY correct way to read a frozen joint-dataset CSV (training_pool
    / calibration_holdout / temporal_holdout / stress / multiseed). Pandas'
    default read_csv treats the literal string "n/a" as a missing-value
    marker -- but "n/a" is a genuine, meaningful candidate value here
    (do_nothing/escalate's method/channel, do_nothing's timing), written by
    the Data Factory's own candidate_generation.do_nothing_candidate() etc.
    Reading with the default na_values would silently corrupt every
    do_nothing/escalate row's candidate_timing/method/channel to NaN --
    caught during Phase 3 feature-construction smoke-testing, before any
    model was trained on the corrupted frame. `keep_default_na=False` +
    `na_values=[""]` preserves "n/a" as a real string while still mapping a
    genuinely empty cell (root_cause/days_overdue for a non-payment_failed /
    non-invoice row, written as an empty field by DataFrame.to_csv for a
    Python None) to real NaN."""
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


_THIS_DIR = Path(__file__).resolve().parent
_ROOT = _THIS_DIR.parent.parent

EVENT_TYPES = {"checkout_abandoned", "payment_failed", "invoice_overdue"}
ACTION_TYPES = {"do_nothing", "retry", "reminder", "payment_link", "escalate"}
ROOT_CAUSES = {"insufficient_funds", "payment_declined", "gateway_timeout",
               "authentication_failed", "expired_card", "network_error"}

CATEGORICAL_FEATURES = [
    "event_type", "root_cause", "current_method", "last_action_type",
    "preferred_channel", "merchant_cohort",
    "candidate_action", "candidate_timing", "candidate_method", "candidate_channel",
    # --- explicit interaction cells (added Phase 3, after diagnosis) ---
    # Pure string concatenations of feature values ALREADY in this contract --
    # no new information, no simulator internals, no hidden_*/analytic_*.
    # They exist because OneHotEncoder + max_depth=4 cannot isolate one cell of
    # the generator's action_effectiveness(action, event_type, root_cause,
    # method_changed) / timing_term(action, root_cause, timing) lookup tables
    # without spending a tree's entire depth budget, leaving that tree no
    # capacity for the continuous context features. Handing the cell over
    # directly removes that representational bottleneck. Measured on
    # model-selection-val only, before adoption: effect-shrinkage slope
    # 0.360 -> 0.471, val AUC 0.6356 -> 0.6447.
    "cell_action_effect", "cell_action_timing",
]
NUMERIC_FEATURES = [
    "amount", "retry_count", "days_since_event", "days_overdue",
    "hours_since_last_action", "prior_contacts_in_window",
    "payment_history_score", "past_recovery_rate",
    "candidate_timing_hours", "candidate_method_changed",
    "network_health_score_rolling", "network_health_success_rate_rolling",
    "network_health_timeout_rate_rolling", "network_health_known",
]
ALL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES

REQUIRED_CONTEXT_KEYS = [
    "event_type", "amount", "current_method", "retry_count", "days_since_event",
    "payment_history_score", "past_recovery_rate", "preferred_channel",
    "merchant_cohort", "bank", "psp", "decision_time_hours",
]
REQUIRED_CANDIDATE_KEYS = ["action_type", "timing", "timing_hours", "method", "channel"]

NETWORK_HEALTH_WINDOW_HOURS = 168.0  # 7 days -- same convention as the Data
                                       # Factory's fatigue_window_hours (Phase 2
                                       # calibration_profiles.py), reused here
                                       # for the network-health rolling window
                                       # too, so the codebase has one trailing-
                                       # window convention rather than two.


class FeatureValidationError(ValueError):
    """Raised on a malformed/out-of-contract context or candidate. Callers
    (ml/inference.py) catch this specifically and return a flagged null
    result -- never a silently-computed score."""


# --------------------------------------------------------------------------- validation

def _is_missing(x) -> bool:
    """True for None AND for a float NaN (pandas' representation of a missing
    value once a CSV round-trips through read_csv) -- callers may legitimately
    hand either shape in, so every 'is this field absent' check in this module
    goes through this one function rather than a bare `x is None` or `x or
    default`, which silently mishandles NaN (NaN is truthy in Python, so
    `nan or default` evaluates to `nan`, not `default` -- a real bug class,
    not a hypothetical one)."""
    if x is None:
        return True
    if isinstance(x, float) and np.isnan(x):
        return True
    return False


def _or_default(x, default):
    return default if _is_missing(x) else x


def _validate_context(context: dict) -> None:
    missing = [k for k in REQUIRED_CONTEXT_KEYS if k not in context]
    if missing:
        raise FeatureValidationError(f"context missing required keys: {missing}")
    if context["event_type"] not in EVENT_TYPES:
        raise FeatureValidationError(f"unknown event_type: {context['event_type']!r}")
    rc = context.get("root_cause")
    if not _is_missing(rc) and rc not in ROOT_CAUSES:
        raise FeatureValidationError(f"unknown root_cause: {rc!r}")
    amount = context["amount"]
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0 \
            or not np.isfinite(amount):
        raise FeatureValidationError(f"amount must be a finite number >= 0, got {amount!r}")
    for k in ("payment_history_score", "past_recovery_rate"):
        v = context[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not np.isfinite(v):
            raise FeatureValidationError(f"{k} must be a finite number, got {v!r}")


def _validate_candidate(candidate: dict) -> None:
    missing = [k for k in REQUIRED_CANDIDATE_KEYS if k not in candidate]
    if missing:
        raise FeatureValidationError(f"candidate missing required keys: {missing}")
    if candidate["action_type"] not in ACTION_TYPES:
        raise FeatureValidationError(f"unknown candidate action_type: {candidate['action_type']!r}")
    th = candidate["timing_hours"]
    if not isinstance(th, (int, float)) or isinstance(th, bool) or th < 0 or not np.isfinite(th):
        raise FeatureValidationError(f"candidate timing_hours must be a finite number >= 0, got {th!r}")


def do_nothing_candidate():
    """Same shape data_factory.candidate_generation.do_nothing_candidate()
    produces -- kept here too so inference callers don't need to import the
    Data Factory package just to score the baseline."""
    return {"action_type": "do_nothing", "timing": "n/a", "timing_hours": 0.0,
            "method": "n/a", "channel": "n/a", "method_changed": False}


# --------------------------------------------------------------------- network health

def load_health_observations(conn) -> pd.DataFrame:
    """Loads the full bank_health_observations table ONCE into a DataFrame.
    Both train_outcome_model.py (batch) and inference.py (live/offline
    harness) call this exact function against the same table -- the shared
    source of truth Decision B2 requires. Small table (~52k rows for one
    seed/profile), safe to hold in memory for the process lifetime."""
    return pd.read_sql_query(
        "SELECT bank, method, psp, window_start, window_end, "
        "success_rate, timeout_rate, health_score FROM bank_health_observations",
        conn,
    )


def network_health_rolling(health_df: pd.DataFrame, bank, method, psp, as_of_hours,
                            window_hours=NETWORK_HEALTH_WINDOW_HOURS):
    """
    Trailing-window aggregate: mean success_rate/timeout_rate/health_score
    over observations for (bank, method, psp) whose window has fully closed
    by as_of_hours (window_end <= as_of_hours, never a future window) and
    whose window_start falls within the trailing window -- temporal-order-
    safe by construction, mirroring bank_health_timeseries.HealthIndex's own
    `window_start <= sim_hour` guard from Phase 2.

    Falls back to the single most recent closed observation (no window
    bound) if the trailing window itself is empty (e.g. very early in the
    horizon). Returns (health_score, success_rate, timeout_rate, known: bool)
    -- known=False (all three None) only when NO closed observation exists
    at all for this channel as of as_of_hours.

    THE SAME FUNCTION is called by train_outcome_model.py (once per training
    row, health_df loaded once) and by inference.py (once per live score,
    health_df loaded once per process) -- this identity is the train/serve
    parity mechanism for the network-health feature.
    """
    sub = health_df[(health_df["bank"] == bank) & (health_df["method"] == method)
                     & (health_df["psp"] == psp) & (health_df["window_end"] <= as_of_hours)]
    if sub.empty:
        return None, None, None, False

    windowed = sub[sub["window_start"] >= as_of_hours - window_hours]
    if windowed.empty:
        windowed = sub.loc[[sub["window_end"].idxmax()]]  # single most recent closed obs

    return (float(windowed["health_score"].mean()),
            float(windowed["success_rate"].mean()),
            float(windowed["timeout_rate"].mean()),
            True)


class NetworkHealthLookup:
    """O(1) trailing-window network-health lookup, precomputed ONCE from the
    same bank_health_observations rows network_health_rolling() reads.

    `network_health_rolling()` above is the semantic reference. This class
    reproduces it EXACTLY (verified by verify_against_reference(), which the
    parity gate asserts returns 0) via per-channel prefix sums: for a query
    `as_of`, the trailing set is exactly {windows with window_end <= as_of
    AND window_start >= as_of - window_hours}, and its mean is a prefix-sum
    difference -- no fixed-size rolling window (which would be off by one
    whenever `as_of` does not land exactly on a 4h boundary, i.e. for almost
    every real sim_hour). Turns a ~55s boolean-mask pass over a 24k-row
    evaluation set into a sub-second one. Both train_outcome_model.py and
    inference.py build ONE of these and hand it to build_feature_row() -- so
    there is still exactly one network-health computation shared by train and
    serve.
    """

    def __init__(self, health_df: pd.DataFrame, window_hours=NETWORK_HEALTH_WINDOW_HOURS):
        self.window_hours = window_hours
        self._by_channel = {}
        cols = ["health_score", "success_rate", "timeout_rate"]
        for (bank, method, psp), g in health_df.groupby(["bank", "method", "psp"], sort=False):
            g = g.sort_values("window_start").reset_index(drop=True)
            arr = g[cols].to_numpy(dtype=float)
            self._by_channel[(bank, method, psp)] = {
                "window_start": g["window_start"].to_numpy(dtype=float),
                "window_end": g["window_end"].to_numpy(dtype=float),
                # prefix sums with a leading zero row -> mean over [lo, hi] is
                # (cumsum[hi+1] - cumsum[lo]) / (hi - lo + 1)
                "cumsum": np.vstack([np.zeros(3), np.cumsum(arr, axis=0)]),
            }

    def get(self, bank, method, psp, as_of_hours):
        ch = self._by_channel.get((bank, method, psp))
        if ch is None:
            return None, None, None, False
        hi = int(np.searchsorted(ch["window_end"], as_of_hours, side="right")) - 1
        if hi < 0:
            return None, None, None, False
        lo = int(np.searchsorted(ch["window_start"], as_of_hours - self.window_hours, side="left"))
        if lo > hi:
            lo = hi  # reference fallback: single most recent closed observation
        n = hi - lo + 1
        s = (ch["cumsum"][hi + 1] - ch["cumsum"][lo]) / n
        return float(s[0]), float(s[1]), float(s[2]), True

    def verify_against_reference(self, health_df, samples):
        """samples: iterable of (bank, method, psp, as_of_hours). Returns the
        max abs difference between this class and network_health_rolling()
        over those samples -- the parity gate asserts it is 0."""
        max_diff = 0.0
        for bank, method, psp, as_of in samples:
            ref = network_health_rolling(health_df, bank, method, psp, as_of, self.window_hours)
            got = self.get(bank, method, psp, as_of)
            if ref[3] != got[3]:
                return float("inf")
            if ref[3]:
                max_diff = max(max_diff, max(abs(ref[k] - got[k]) for k in range(3)))
        return max_diff


# ------------------------------------------------------------------------- feature rows

def build_feature_row(context: dict, candidate: dict, health_lookup: "NetworkHealthLookup") -> dict:
    """One row of ALL_FEATURES for one (context, candidate) pair. Raises
    FeatureValidationError on a malformed input -- never silently coerces."""
    _validate_context(context)
    _validate_candidate(candidate)

    health_score, success_rate, timeout_rate, known = health_lookup.get(
        context["bank"], context["current_method"], context["psp"],
        context["decision_time_hours"],
    )

    # Normalized once, then reused -- both for the plain features and for the
    # interaction cells below, so the cells can never disagree with the columns
    # they are built from (and the batch path, which routes through this same
    # function, cannot diverge from the single-case serving path).
    event_type = context["event_type"]
    root_cause = _or_default(context.get("root_cause"), "none")
    candidate_action = candidate["action_type"]
    candidate_timing = candidate["timing"]
    method_changed = float(bool(candidate.get("method_changed", False)))

    return {
        "event_type": event_type,
        "root_cause": root_cause,
        "current_method": context["current_method"],
        "last_action_type": _or_default(context.get("last_action_type"), "none"),
        "preferred_channel": context["preferred_channel"],
        "merchant_cohort": context["merchant_cohort"],
        "candidate_action": candidate_action,
        "candidate_timing": candidate_timing,
        "candidate_method": candidate["method"],
        "candidate_channel": candidate["channel"],
        "cell_action_effect": f"{candidate_action}|{event_type}|{root_cause}|{int(method_changed)}",
        "cell_action_timing": f"{candidate_action}|{root_cause}|{candidate_timing}",
        "amount": float(context["amount"]),
        "retry_count": float(context["retry_count"]),
        "days_since_event": float(context["days_since_event"]),
        "days_overdue": float(_or_default(context.get("days_overdue"), 0.0)),
        "hours_since_last_action": float(_or_default(context.get("hours_since_last_action"), 0.0)),
        "prior_contacts_in_window": float(_or_default(context.get("prior_contacts_in_window"), 0)),
        "payment_history_score": float(context["payment_history_score"]),
        "past_recovery_rate": float(context["past_recovery_rate"]),
        "candidate_timing_hours": float(candidate["timing_hours"]),
        "candidate_method_changed": method_changed,
        "network_health_score_rolling": health_score,
        "network_health_success_rate_rolling": success_rate,
        "network_health_timeout_rate_rolling": timeout_rate,
        "network_health_known": float(known),
    }


def context_and_candidate_from_joint_row(row: dict) -> tuple:
    """Reconstructs the (context, candidate) pair a live caller would have
    passed, from one row of a frozen joint-dataset CSV (as a dict, e.g. from
    df.to_dict('records'))."""
    context = {
        "event_type": row["event_type"], "root_cause": row["root_cause"],
        "amount": row["amount"], "current_method": row["current_method"],
        "retry_count": row["retry_count"], "days_since_event": row["days_since_event"],
        "days_overdue": row["days_overdue"], "last_action_type": row["last_action_type"],
        "hours_since_last_action": row["hours_since_last_action"],
        "prior_contacts_in_window": row["prior_contacts_in_window"],
        "merchant_cohort": row["merchant_cohort"],
        "payment_history_score": row["payment_history_score"],
        "past_recovery_rate": row["past_recovery_rate"],
        "preferred_channel": row["preferred_channel"],
        "bank": row["bank"], "psp": row["psp"],
        "decision_time_hours": row["sim_hour"],
    }
    candidate = {
        "action_type": row["candidate_action"], "timing": row["candidate_timing"],
        "timing_hours": row["candidate_timing_hours"], "method": row["candidate_method"],
        "method_changed": bool(row["candidate_method_changed"]), "channel": row["candidate_channel"],
    }
    return context, candidate


def build_feature_frame_from_joint_df(joint_df: pd.DataFrame,
                                       health_lookup: "NetworkHealthLookup") -> pd.DataFrame:
    """Batch path: one feature row per row of a frozen joint-dataset CSV
    (training_pool / calibration_holdout / temporal_holdout / stress /
    multiseed -- all share this exact column layout).

    Deliberately implemented as a loop calling build_feature_row() (the exact
    same function ml.inference.score_candidate() calls for one live case) via
    context_and_candidate_from_joint_row() -- NOT a second, independently-
    vectorized re-implementation of the same column logic. Two
    implementations that merely happen to currently agree is exactly the
    train/serve divergence risk Decision B2 / the parity gate exist to
    eliminate; calling through one function is what makes the parity
    guarantee structural rather than coincidental. (The network-health
    lookup IS precomputed in bulk -- see NetworkHealthLookup -- but that is a
    single shared lookup object both paths use, not a divergent second
    implementation of the feature row itself.)"""
    records = joint_df.to_dict("records")
    rows = []
    for r in records:
        context, candidate = context_and_candidate_from_joint_row(r)
        rows.append(build_feature_row(context, candidate, health_lookup))
    return pd.DataFrame(rows, columns=ALL_FEATURES)
