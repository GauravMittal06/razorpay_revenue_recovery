"""
Data Factory -- bank/method/PSP network-health time series. Phase 2.

Generated as part of the same synthetic world as everything else in this
package (not bolted on after the fact): a SyntheticWorld's bank_channels
each get a health series covering the full simulated time horizon before
any case is generated, so every case's network-health feature is a
lookup into an already-materialized series, exactly mirroring how
Phase 3's live inference module will look up a rolling aggregate from the
real `bank_health_observations` table.

Random-walk baseline + occasional multi-window "incident" dips, per
(bank, method, psp). success_rate and timeout_rate are derived from the
same underlying health_score so they move together directionally, as they
would in reality (a degraded bank/PSP shows both lower success and higher
timeout rates, not independently random ones).
"""

from dataclasses import dataclass
import numpy as np

WINDOW_HOURS = 4  # each observation window covers 4 simulated hours


@dataclass
class HealthObservation:
    bank: str
    method: str
    psp: str
    window_start: float  # simulated hour
    window_end: float
    success_rate: float
    timeout_rate: float
    health_score: float


def clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def generate_series_for_channel(rng, bank, method, psp, horizon_hours, profile):
    """One (bank, method, psp)'s full health series across the simulated
    horizon. incident_active tracks a multiplicative dip that persists for
    several consecutive windows (an outage looks like a dip that lasts,
    not an independent bad draw every window)."""
    n_windows = int(np.ceil(horizon_hours / WINDOW_HOURS))
    base = clip01(rng.beta(*profile.bank_health_beta))

    obs = []
    health = base
    incident_windows_remaining = 0
    incident_multiplier = 1.0

    for w in range(n_windows):
        window_start = w * WINDOW_HOURS
        window_end = window_start + WINDOW_HOURS

        # Random-walk drift toward/around the channel's own base level.
        health = clip01(
            health + rng.normal(0.0, profile.bank_health_walk_sigma)
            + 0.05 * (base - health)  # mild mean reversion
        )

        # Incident onset / continuation.
        if incident_windows_remaining > 0:
            incident_windows_remaining -= 1
        elif rng.random() < profile.incident_probability_per_window:
            incident_windows_remaining = int(rng.integers(2, 7))  # 8-28h incident
            lo, hi = profile.incident_severity_range
            incident_multiplier = float(rng.uniform(lo, hi))

        if incident_windows_remaining > 0:
            observed_health = clip01(health * incident_multiplier)
        else:
            incident_multiplier = 1.0
            observed_health = health

        # success_rate and timeout_rate both derive from observed_health,
        # so they move together directionally rather than independently.
        success_rate = clip01(0.55 + 0.42 * observed_health + rng.normal(0, 0.02))
        timeout_rate = clip01(0.03 + 0.25 * (1 - observed_health) + rng.normal(0, 0.01))

        obs.append(HealthObservation(
            bank=bank, method=method, psp=psp,
            window_start=window_start, window_end=window_end,
            success_rate=round(success_rate, 4),
            timeout_rate=round(timeout_rate, 4),
            health_score=round(observed_health, 4),
        ))

    return obs


def generate_bank_health_timeseries(rng, world, horizon_hours, profile):
    """Full series for every bank_channel in the world. Returns a flat
    list of HealthObservation plus an index for O(1) lookup by
    (bank, method, psp, sim_hour)."""
    all_obs = []
    for ch in world.bank_channels:
        all_obs.extend(generate_series_for_channel(rng, ch.bank, ch.method, ch.psp, horizon_hours, profile))
    return all_obs


class HealthIndex:
    """O(1)-ish lookup of the health observation covering a given
    simulated hour, for a given (bank, method, psp). Built once per run."""

    def __init__(self, observations):
        self._by_channel = {}
        for o in observations:
            key = (o.bank, o.method, o.psp)
            self._by_channel.setdefault(key, []).append(o)
        for key in self._by_channel:
            self._by_channel[key].sort(key=lambda o: o.window_start)

    def lookup(self, bank, method, psp, sim_hour):
        series = self._by_channel.get((bank, method, psp))
        if not series:
            return None
        idx = int(sim_hour // WINDOW_HOURS)
        idx = max(0, min(idx, len(series) - 1))
        obs = series[idx]
        # Guard: only return an observation whose window has actually
        # started by sim_hour -- this is the temporal-order invariant
        # (see validators.leakage_temporal_order) applied at the source.
        if obs.window_start > sim_hour:
            idx = max(0, idx - 1)
            obs = series[idx]
        return obs
