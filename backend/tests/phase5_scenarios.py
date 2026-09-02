"""
Phase 5 / W1 -- the golden decision corpus.

WHY THIS EXISTS
---------------
Phase 5 adds an optional `ranked_candidates` parameter to `decide_action()`.
The EXECUTION_PLAN requires that with the optimizer disabled, behaviour is
"unchanged from the existing hardcoded logic" -- and the Phase 5 acceptance
gate requires that backward compatibility be *proven*, not asserted.

This module freezes the pre-change output of `decide_action()` across every
branch it can take. It is captured BEFORE the first edit to decide_action.py,
which is the only moment the corpus can honestly be called a baseline.

Deliberately NOT a `test_*` module: pytest must not collect it. It is imported
by tests/test_phase5_regression.py, and can be re-run standalone to regenerate
the corpus (see __main__ at the bottom) -- though regenerating it after
decide_action.py changes would defeat its entire purpose, so that path prints
a warning and requires an explicit flag.

DETERMINISM
-----------
Three things could make a captured decision non-reproducible. All three are
pinned here:

1. `decide_action()` reads `int(time.time())`. The capture freezes it at
   FROZEN_NOW, so age/cooldown arithmetic -- which is embedded verbatim in the
   `reasoning` strings ("18.0h remaining") -- is stable.

2. The 9am-8pm contact-window check reads the *local* hour of `created_at`
   (`datetime.fromtimestamp(...).hour`), so a fixed epoch literal would land in
   a different branch in a different timezone. Every timestamp here is instead
   built from a local wall-clock datetime, so "12:00" is noon in any timezone
   and the epoch value it resolves to is never itself compared.

3. `ml_recovery_probability` comes from the advisory xgb model. It is a
   gitignored artifact, so a fresh worktree has no model and the field comes
   back None, while a checkout that has one returns a float. The corpus
   records which regime it was captured under (see `capture_all`) and the
   comparison test refuses to compare across regimes rather than silently
   passing on a corpus of Nones.

Self-contained on purpose: the row-insert helpers below duplicate a little of
tests/conftest.py rather than importing it. A golden baseline that silently
changes shape because a shared fixture default was edited is not a baseline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "phase5_decide_action_golden.json"

# Local wall-clock anchor. The date is arbitrary and fixed; what matters is
# that it is noon local time, so scenarios can offset to any hour and land
# inside or outside the contact window deterministically.
_ANCHOR = datetime(2026, 6, 15, 12, 0, 0)
FROZEN_NOW = int(_ANCHOR.timestamp())

HOUR = 3600
DAY = 86400


def at_local(hour: int = 12, days_ago: int = 0, hours_ago: int = 0) -> int:
    """
    A timestamp at a specific *local* hour, N days/hours before the anchor.

    Built by constructing the datetime with the hour set explicitly, so the
    local hour survives any timezone; only the resulting epoch differs, and
    the epoch value is never part of a golden comparison.
    """
    dt = _ANCHOR.replace(hour=hour) - timedelta(days=days_ago, hours=hours_ago)
    return int(dt.timestamp())


# --------------------------------------------------------------------------
# Row constructors -- intentionally local to this module
# --------------------------------------------------------------------------

def _opportunity(conn, opportunity_id, **overrides):
    row = {
        "opportunity_id": opportunity_id,
        "merchant_id": None,
        "customer_id": None,
        "event_type": "payment_failed",
        "root_cause": "gateway_timeout",
        "amount_at_risk": 50_000,
        "days_overdue": None,
        "status": "open",
        "created_at": at_local(hour=12, days_ago=1),
        "resolved_at": None,
        "recovered_bool": None,
        "partial_recovery_amount": None,
        "recovered_at": None,
        "time_to_recovery": None,
        "resolution_type": None,
        "ingestion_event_id": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    ph = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO opportunities ({cols}) VALUES ({ph})", row)
    conn.commit()
    return row


def _payment(conn, opportunity_id, payment_id, **overrides):
    row = {
        "id": payment_id,
        "opportunity_id": opportunity_id,
        "entity": "payment",
        "amount": 50_000,
        "currency": "INR",
        "status": "failed",
        "order_id": None,
        "invoice_id": None,
        "method": "card",
        "email": None,
        "contact": None,
        "error_code": None,
        "error_description": None,
        "error_source": None,
        "error_step": None,
        "error_reason": "gateway_timeout",
        "created_at": at_local(hour=12, days_ago=1),
    }
    row.update(overrides)
    cols = ", ".join(row)
    ph = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO payments ({cols}) VALUES ({ph})", row)
    conn.commit()
    return row


def _decision(conn, opportunity_id, action_type, outcome="executed", timestamp=None):
    conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, candidate_id, action_type, outcome, reasoning,
         triggered_by, ml_recovery_probability, flag_type, timestamp)
        VALUES (?, NULL, ?, ?, 'golden fixture', 'rule', NULL, NULL, ?)
        """,
        (opportunity_id, action_type, outcome,
         FROZEN_NOW if timestamp is None else timestamp),
    )
    conn.commit()


def _message(conn, opportunity_id, sender="customer", timestamp=None):
    conn.execute(
        """
        INSERT INTO messages
        (opportunity_id, sender, content, intent_extracted, intent_confidence,
         mentioned_reason, timestamp)
        VALUES (?, ?, 'golden fixture', NULL, NULL, NULL, ?)
        """,
        (opportunity_id, sender, FROZEN_NOW if timestamp is None else timestamp),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Scenarios -- one per reachable return statement in decide_action(), plus
# the variants that change which branch is reached or what it emits.
# --------------------------------------------------------------------------
#
# Each builder receives (conn, oid) and returns the kwargs to pass to
# decide_action() alongside the opportunity it just constructed.

def _clean_payment_failed(conn, oid):
    _opportunity(conn, oid)
    _payment(conn, oid, f"pay_{oid}")
    return {}


def _clean_checkout_abandoned(conn, oid):
    _opportunity(conn, oid, event_type="checkout_abandoned", root_cause=None)
    return {}


def _clean_invoice_overdue(conn, oid):
    _opportunity(conn, oid, event_type="invoice_overdue", root_cause=None, days_overdue=3)
    return {}


def _invoice_deeply_overdue(conn, oid):
    # days_overdue > 14 flips default_action to escalate, but days_overdue >= 7
    # also trips the auto-escalate branch first -- both routes are captured so
    # the precedence between them is pinned, not assumed.
    _opportunity(conn, oid, event_type="invoice_overdue", root_cause=None, days_overdue=20)
    return {}


def _invoice_overdue_at_autostop(conn, oid):
    _opportunity(conn, oid, event_type="invoice_overdue", root_cause=None, days_overdue=7)
    return {}


def _invoice_overdue_below_autostop(conn, oid):
    _opportunity(conn, oid, event_type="invoice_overdue", root_cause=None, days_overdue=6)
    return {}


def _already_stopped(conn, oid):
    _opportunity(conn, oid)
    _decision(conn, oid, "stop", "executed", at_local(hour=12, days_ago=2))
    return {}


def _already_escalated(conn, oid):
    _opportunity(conn, oid)
    _decision(conn, oid, "escalate", "executed", at_local(hour=12, days_ago=2))
    return {}


def _dispute(conn, oid):
    _opportunity(conn, oid)
    return {"dispute_flag": True}


def _low_confidence(conn, oid):
    _opportunity(conn, oid)
    return {"extracted_intent": "will_pay_later", "intent_confidence": 0.42}


def _confidence_at_threshold(conn, oid):
    # 0.6 is NOT below 0.6 -- pins the boundary as inclusive-pass.
    _opportunity(conn, oid)
    return {"extracted_intent": "will_pay_later", "intent_confidence": 0.6}


def _intent_mismatch(conn, oid):
    _opportunity(conn, oid, root_cause="insufficient_funds")
    return {"extracted_intent": "will_pay_later",
            "intent_confidence": 0.9,
            "mentioned_reason": "expired_card"}


def _method_update_is_not_a_conflict(conn, oid):
    # payment_method_updated against a method-class root cause is a
    # log-only flag, not a blocking mismatch.
    _opportunity(conn, oid, root_cause="expired_card")
    _payment(conn, oid, f"pay_{oid}", error_reason="expired_card")
    return {"extracted_intent": "payment_method_updated",
            "intent_confidence": 0.9,
            "mentioned_reason": "expired_card"}


def _seven_days_silence(conn, oid):
    _opportunity(conn, oid, created_at=at_local(hour=12, days_ago=9))
    return {}


def _seven_days_but_customer_replied(conn, oid):
    _opportunity(conn, oid, created_at=at_local(hour=12, days_ago=9))
    _message(conn, oid, sender="customer", timestamp=at_local(hour=12, days_ago=1))
    return {}


def _agent_message_is_not_a_reply(conn, oid):
    _opportunity(conn, oid, created_at=at_local(hour=12, days_ago=9))
    _message(conn, oid, sender="agent", timestamp=at_local(hour=12, days_ago=1))
    return {}


def _max_retries(conn, oid):
    _opportunity(conn, oid)
    for i, d in enumerate((5, 4, 3)):
        _decision(conn, oid, "retry", "executed", at_local(hour=12, days_ago=d))
    return {}


def _reminders_count_toward_ceiling(conn, oid):
    _opportunity(conn, oid)
    for d in (5, 4, 3):
        _decision(conn, oid, "reminder", "executed", at_local(hour=12, days_ago=d))
    return {}


def _blocked_attempts_do_not_consume_budget(conn, oid):
    _opportunity(conn, oid)
    for d in (5, 4, 3):
        _decision(conn, oid, "retry", "blocked_cooldown", at_local(hour=12, days_ago=d))
    return {}


def _cooldown_active(conn, oid):
    _opportunity(conn, oid)
    _decision(conn, oid, "retry", "executed", FROZEN_NOW - 6 * HOUR)
    return {}


def _cooldown_just_expired(conn, oid):
    _opportunity(conn, oid)
    _decision(conn, oid, "retry", "executed", FROZEN_NOW - 25 * HOUR)
    return {}


def _outside_contact_window(conn, oid):
    _opportunity(conn, oid, created_at=at_local(hour=3, days_ago=1))
    return {}


def _contact_window_lower_boundary(conn, oid):
    _opportunity(conn, oid, created_at=at_local(hour=9, days_ago=1))
    return {}


def _contact_window_upper_boundary(conn, oid):
    # 20 is exclusive -- 20:00 is outside.
    _opportunity(conn, oid, created_at=at_local(hour=20, days_ago=1))
    return {}


def _escalate_ignores_contact_window(conn, oid):
    # default_action is escalate (deeply overdue invoice), so the contact-hours
    # check is bypassed even at 3am.
    _opportunity(conn, oid, event_type="invoice_overdue", root_cause=None,
                 days_overdue=20, created_at=at_local(hour=3, days_ago=1))
    return {}


SCENARIOS = [
    ("clean_payment_failed", _clean_payment_failed),
    ("clean_checkout_abandoned", _clean_checkout_abandoned),
    ("clean_invoice_overdue", _clean_invoice_overdue),
    ("invoice_deeply_overdue", _invoice_deeply_overdue),
    ("invoice_overdue_at_autostop", _invoice_overdue_at_autostop),
    ("invoice_overdue_below_autostop", _invoice_overdue_below_autostop),
    ("already_stopped", _already_stopped),
    ("already_escalated", _already_escalated),
    ("dispute", _dispute),
    ("low_confidence", _low_confidence),
    ("confidence_at_threshold", _confidence_at_threshold),
    ("intent_mismatch", _intent_mismatch),
    ("method_update_is_not_a_conflict", _method_update_is_not_a_conflict),
    ("seven_days_silence", _seven_days_silence),
    ("seven_days_but_customer_replied", _seven_days_but_customer_replied),
    ("agent_message_is_not_a_reply", _agent_message_is_not_a_reply),
    ("max_retries", _max_retries),
    ("reminders_count_toward_ceiling", _reminders_count_toward_ceiling),
    ("blocked_attempts_do_not_consume_budget", _blocked_attempts_do_not_consume_budget),
    ("cooldown_active", _cooldown_active),
    ("cooldown_just_expired", _cooldown_just_expired),
    ("outside_contact_window", _outside_contact_window),
    ("contact_window_lower_boundary", _contact_window_lower_boundary),
    ("contact_window_upper_boundary", _contact_window_upper_boundary),
    ("escalate_ignores_contact_window", _escalate_ignores_contact_window),
]


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def _ml_regime() -> str:
    """
    Which advisory-model regime this capture ran under. Recorded in the corpus
    so a comparison can refuse to run across a mismatch instead of trivially
    passing on a corpus where every ml_recovery_probability is None.
    """
    from backend.engine import decide_action as da

    path = Path(da.__file__).resolve().parent.parent / "ml" / "models" / "xgb_model.joblib"
    return "model_present" if path.exists() else "model_absent"


def capture_all(conn) -> dict:
    """
    Build every scenario against `conn` and return the decision each produces.

    One connection serves all scenarios: every compliance lookup in
    decide_action() is keyed by opportunity_id, so distinct ids cannot
    interfere, and a shared connection keeps the capture cheap.

    The clock is frozen for the whole capture. Note this patches `time.time`
    as decide_action.py resolves it, which is the stdlib function -- deliberate
    and scoped to the capture.
    """
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action

    captured = {}
    with mock.patch("backend.engine.decide_action.time.time", return_value=float(FROZEN_NOW)):
        for name, build in SCENARIOS:
            oid = f"opp_golden_{name}"
            kwargs = build(conn, oid)

            opportunity = dict(conn.execute(
                "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)
            ).fetchone())
            row = conn.execute(
                "SELECT * FROM payments WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
                (oid,),
            ).fetchone()
            latest_payment = dict(row) if row else None

            classification = classify(
                opportunity["event_type"],
                latest_payment.get("error_reason") if latest_payment
                else opportunity.get("root_cause"),
            )

            decision = decide_action(opportunity, classification, conn,
                                     latest_payment=latest_payment, **kwargs)

            # Captured verbatim, including which keys are absent -- several
            # branches omit flag_type/ml_recovery_probability entirely and that
            # asymmetry is part of the contract being frozen.
            captured[name] = decision

    return captured


def build_corpus(conn) -> dict:
    return {
        "schema_version": 1,
        "captured_against": "decide_action.py prior to any Phase 5 modification",
        "frozen_now_local": _ANCHOR.isoformat(),
        "ml_regime": _ml_regime(),
        "scenario_count": len(SCENARIOS),
        "decisions": capture_all(conn),
    }


def _fresh_conn(path):
    from backend.db import db as db_module
    from backend.db.db import create_schema, get_connection

    db_module.DB_PATH = path
    conn = get_connection()
    create_schema(conn)
    return conn


if __name__ == "__main__":
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="write the corpus to its golden path")
    ap.add_argument("--i-understand-this-invalidates-the-baseline", action="store_true",
                    help="required alongside --write once decide_action.py has been modified")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(Path(tmp) / "golden.db")
        corpus = build_corpus(conn)
        conn.close()

    text = json.dumps(corpus, indent=2, sort_keys=True) + "\n"

    if not args.write:
        print(text)
        raise SystemExit(0)

    if GOLDEN_PATH.exists() and not args.i_understand_this_invalidates_the_baseline:
        raise SystemExit(
            f"refusing to overwrite an existing baseline at {GOLDEN_PATH}.\n"
            "The corpus is only meaningful as a pre-change capture; regenerating\n"
            "it after decide_action.py changes would make the regression test\n"
            "compare the new behaviour against itself. Pass\n"
            "--i-understand-this-invalidates-the-baseline if that is genuinely intended."
        )

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {GOLDEN_PATH}  ({corpus['scenario_count']} scenarios, "
          f"ml_regime={corpus['ml_regime']})")
