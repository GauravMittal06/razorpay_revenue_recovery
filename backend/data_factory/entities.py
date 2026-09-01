"""
Data Factory -- persistent synthetic entities. Phase 2.

These entities are genuinely persistent across the simulated timeline: a
customer object created here accumulates a real contact_history as cases
are generated against them in temporal order, so a repeat customer's
behavior in one opportunity is informed by their history in prior ones
(this is what backs the intervention-fatigue feature and the
customer-level leakage check -- a customer, not a case, is the unit that
must never straddle a train/test split boundary).

Distinct from backend/data/generate_seed_data.py: that module produces a
small, demo/UI-facing dataset written straight to the production schema
(merchants.json / customers.json / ...). This module produces a larger,
richer, offline-only synthetic population -- with mutable behavioral
state a demo dataset has no reason to carry -- that only ever feeds the
Data Factory. Nothing here is ever written to backend/data/*.json or to
the production `merchants`/`customers` tables.
"""

from dataclasses import dataclass, field
import numpy as np

MERCHANT_COHORTS = ["d2c", "smb", "enterprise", "marketplace"]
CHANNELS = ["email", "sms", "whatsapp"]
BANKS = ["hdfc", "icici", "sbi", "axis", "kotak", "yes_bank"]
METHODS = ["card", "netbanking", "upi", "wallet"]
PSPS = ["razorpay_gw_a", "razorpay_gw_b", "razorpay_gw_c"]


def clip01(x):
    return float(np.clip(x, 0.0, 1.0))


@dataclass
class Merchant:
    merchant_id: str
    name: str
    cohort: str


@dataclass
class Customer:
    customer_id: str
    merchant_id: str
    payment_history_score: float
    past_recovery_rate: float
    preferred_channel: str
    preferred_method: str
    # Mutable, persistent across the whole generation run, appended to in
    # strictly increasing simulated-time order as cases are generated
    # against this customer -- this IS the fatigue/history mechanism.
    contact_history: list = field(default_factory=list)  # list of sim_hour floats

    def contacts_in_window(self, as_of_hour: float, window_hours: float) -> int:
        """Count prior contacts strictly before as_of_hour, within the
        trailing window -- 'strictly before' is what keeps this a
        temporally-valid feature (see validators.leakage_temporal_order)."""
        return sum(
            1 for t in self.contact_history
            if as_of_hour - window_hours <= t < as_of_hour
        )

    def record_contact(self, sim_hour: float) -> None:
        self.contact_history.append(sim_hour)


@dataclass
class BankChannel:
    """One (bank, method, psp) triple -- the unit bank_health_timeseries.py
    generates a health series for."""
    bank: str
    method: str
    psp: str

    @property
    def key(self):
        return (self.bank, self.method, self.psp)


def generate_merchants(rng, n_merchants: int) -> list:
    names = [
        "Kirana Direct", "Bloom & Basket", "Vertex Fitness", "Northline Logistics",
        "Paperplane Stationery", "Solstice Apparel", "Ember Home Goods",
        "Cobalt Electronics", "Meridian Wellness", "Driftwood Furniture",
    ]
    merchants = []
    for i in range(n_merchants):
        merchants.append(Merchant(
            merchant_id=f"dfmerch_{i:03d}",
            name=names[i % len(names)],
            cohort=str(rng.choice(MERCHANT_COHORTS)),
        ))
    return merchants


def generate_customers(rng, merchants: list, n_customers: int, profile) -> list:
    customers = []
    for i in range(n_customers):
        merchant = merchants[int(rng.integers(0, len(merchants)))]
        customers.append(Customer(
            customer_id=f"dfcust_{i:05d}",
            merchant_id=merchant.merchant_id,
            payment_history_score=round(clip01(rng.beta(*profile.customer_history_beta)), 4),
            past_recovery_rate=round(clip01(rng.beta(*profile.customer_recovery_beta)), 4),
            preferred_channel=str(rng.choice(CHANNELS)),
            preferred_method=str(rng.choice(METHODS)),
        ))
    return customers


def generate_bank_channels(rng, n_channels: int = None) -> list:
    """All (bank, method, psp) triples, or a random subset if n_channels
    is given. Deterministic given rng -- exhaustive by default since the
    cross product (6 banks x 4 methods x 3 psps = 72) is small."""
    all_triples = [(b, m, p) for b in BANKS for m in METHODS for p in PSPS]
    if n_channels is None or n_channels >= len(all_triples):
        chosen = all_triples
    else:
        idx = rng.choice(len(all_triples), size=n_channels, replace=False)
        chosen = [all_triples[i] for i in sorted(idx)]
    return [BankChannel(bank=b, method=m, psp=p) for (b, m, p) in chosen]


class SyntheticWorld:
    """Container for one generation run's persistent entity population.
    Constructed once per (seed, profile) and threaded through every case
    generated in that run -- this is what makes 'persistent across
    simulated time' true rather than aspirational."""

    def __init__(self, rng, profile, n_merchants=8, n_customers=300):
        self.profile = profile
        self.merchants = generate_merchants(rng, n_merchants)
        self.customers = generate_customers(rng, self.merchants, n_customers, profile)
        self.bank_channels = generate_bank_channels(rng)
        self._customer_by_id = {c.customer_id: c for c in self.customers}
        self._merchant_by_id = {m.merchant_id: m for m in self.merchants}

    def random_customer(self, rng) -> Customer:
        return self.customers[int(rng.integers(0, len(self.customers)))]

    def customer(self, customer_id: str) -> Customer:
        return self._customer_by_id[customer_id]

    def merchant(self, merchant_id: str) -> Merchant:
        return self._merchant_by_id[merchant_id]
