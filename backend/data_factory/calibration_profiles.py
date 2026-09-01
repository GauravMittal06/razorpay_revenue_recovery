"""
Data Factory calibration profiles -- Phase 2.

A calibration profile is a named, versioned set of distributional
parameters consumed by every generation module in this package
(entities.py, bank_health_timeseries.py, candidate_outcome_dataset.py).
All profiles share the exact same generator code path; only the numbers
below differ. This is what Section 5 / Phase 2 of the execution plan means
by "at least two distinct, named calibration profiles ... sharing
generator code but genuinely different distributional parameters."

Do not add profile-specific branches anywhere else in this package --
every profile-dependent choice must route through a value read from the
active CalibrationProfile instance, so that adding a third profile later
never requires touching generation logic, only this file.
"""

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    version: str

    # --- entities.py: persistent synthetic population shape ---
    # Beta(a, b) parameters for payment_history_score / past_recovery_rate
    # at customer-creation time. Higher a/b ratio -> healthier population.
    customer_history_beta: tuple
    customer_recovery_beta: tuple

    # --- bank_health_timeseries.py ---
    # Beta(a, b) for baseline per-(bank, method, psp) health_score, and the
    # standard deviation of the random-walk step applied at each time window.
    bank_health_beta: tuple
    bank_health_walk_sigma: float
    # Probability, per window, that a bank/method/psp enters a degraded
    # "incident" state (a multi-window dip) -- this is what should differ
    # most sharply between baseline and stress.
    incident_probability_per_window: float
    incident_severity_range: tuple  # (min, max) multiplicative health hit

    # --- candidate_outcome_dataset.py: outcome generative function ---
    # Global intercept shift applied to every candidate's logit -- shifts
    # the whole world's baseline recoverability up/down.
    global_intercept_shift: float
    # Weight applied to the (bank/method) network-health feature inside the
    # outcome logit. Higher magnitude -> network conditions matter more.
    network_health_weight: float
    # Fatigue: logit penalty per prior contact in the trailing fatigue
    # window, and the window length itself (hours).
    fatigue_penalty_per_contact: float
    fatigue_window_hours: float
    # Outcome noise (rng.normal(0, sigma) added to every candidate's logit).
    outcome_noise_sigma: float
    # Partial-recovery: given a recovery occurred, probability it is only
    # partial, and the Beta(a, b) fraction-of-amount-recovered when partial.
    partial_recovery_probability: float
    partial_recovery_fraction_beta: tuple

    def as_dict(self) -> dict:
        return asdict(self)


# Baseline: the "normal operating conditions" profile -- roughly matched in
# central tendency to the pre-Phase-2 simulator's implicit assumptions
# (see backend/data_factory/legacy/simulate_training_data_frozen.py), so
# that the ground-truth / semantic-equivalence check in Section 5, Phase 2
# has something meaningful to compare against.
BASELINE = CalibrationProfile(
    name="baseline",
    version="1.0.0",
    customer_history_beta=(5, 3),
    customer_recovery_beta=(4, 3),
    bank_health_beta=(9, 2),
    bank_health_walk_sigma=0.015,
    incident_probability_per_window=0.02,
    incident_severity_range=(0.35, 0.65),
    global_intercept_shift=0.0,
    network_health_weight=0.9,
    fatigue_penalty_per_contact=0.12,
    fatigue_window_hours=168.0,  # 7 days
    outcome_noise_sigma=0.15,
    partial_recovery_probability=0.20,
    partial_recovery_fraction_beta=(6, 2),
)

# Stress: a genuinely worse macro environment -- more bank/PSP incidents,
# a lower and more volatile baseline network health, a harsher fatigue
# penalty (customers are more contact-averse when everything is already
# failing more), and a lower global intercept. These are DISTRIBUTIONAL
# changes, not a different seed -- verified materially different in
# PHASE2_NOTES.md via a direct comparison of generated bank_health_score
# and outcome-rate distributions between profiles (same seed, same code).
STRESS = CalibrationProfile(
    name="stress",
    version="1.0.0",
    customer_history_beta=(3, 4),          # skews lower than baseline
    customer_recovery_beta=(2.5, 4),        # skews lower than baseline
    bank_health_beta=(5, 4),                # materially lower & wider than baseline (9,2)
    bank_health_walk_sigma=0.045,           # 3x baseline volatility
    incident_probability_per_window=0.09,   # ~4.5x baseline incident rate
    incident_severity_range=(0.15, 0.55),   # deeper dips than baseline
    global_intercept_shift=-0.35,
    network_health_weight=1.3,              # network conditions bite harder
    fatigue_penalty_per_contact=0.22,       # customers more contact-averse
    fatigue_window_hours=168.0,
    outcome_noise_sigma=0.18,
    partial_recovery_probability=0.30,      # more partial, fewer full recoveries
    partial_recovery_fraction_beta=(4, 3),
)

PROFILES = {
    "baseline": BASELINE,
    "stress": STRESS,
}


def get_profile(name: str) -> CalibrationProfile:
    if name not in PROFILES:
        raise ValueError(f"Unknown calibration profile '{name}'. Known: {list(PROFILES)}")
    return PROFILES[name]
