"""
mark_opportunity_recovered(): the manual-confirmation operational utility.

EXECUTION_PLAN Phase 6 keeps this deliberately -- "a manual confirmation
(retained as a legitimate, clearly-labeled operational utility for testing and
demonstration)". It is how a human confirms, in the Live Agent Console, that
money actually came back.

WHAT CHANGED IN PHASE 6 / X4
    It no longer writes the business-outcome columns itself. It is now a thin,
    clearly-labeled wrapper over `observe_outcome()`, which is the single path
    by which any outcome reaches an opportunity, from any source.

    The compare-and-swap this function used to own moved into observe_outcome
    unchanged -- it was the better of the two implementations that existed
    (execute_action's terminal branch had no such guard), so unification
    adopted it rather than averaging them.

    Its return shape is preserved exactly, because api/actions.simulate_recovery
    and the console depend on it: status "ok" / "already_recovered" /
    "rejected_stopped" / "opportunity_not_found", with opportunity_status,
    recovered_at and partial_recovery_amount on the success path. The mapping
    from observe_outcome's vocabulary back to that shape is the only logic
    left in this file.

    It records source="manual_confirmation", which is what makes a
    human-confirmed outcome distinguishable from a real payment event in the
    data rather than only in the narration.
"""

from backend.db.db import OUTCOME_SOURCES
from backend.engine.observe_outcome import observe_outcome

# Named rather than inlined so the static gate's allowlist and this caller
# cannot drift apart silently.
SOURCE = "manual_confirmation"
assert SOURCE in OUTCOME_SOURCES  # noqa: S101 -- import-time sanity only


def mark_opportunity_recovered(opportunity_id: str, conn,
                               partial_recovery_amount: int = None) -> dict:
    result = observe_outcome(
        opportunity_id, conn,
        resolution="recovered",
        source=SOURCE,
        partial_recovery_amount=partial_recovery_amount,
    )

    if result["result"] == "opportunity_not_found":
        return {"opportunity_id": opportunity_id,
                "status": "opportunity_not_found"}

    if result["result"] == "already_resolved":
        # The caller's two distinct rejection reasons. `stopped` and
        # `recovered` are different answers to "why did this not take effect",
        # and the console shows them differently.
        return {
            "opportunity_id": opportunity_id,
            "status": ("rejected_stopped" if result.get("status") == "stopped"
                       else "already_recovered"),
            "opportunity_status": result.get("status"),
        }

    return {
        "opportunity_id": opportunity_id,
        "status": "ok",
        "opportunity_status": "recovered",
        "recovered_at": result["recovered_at"],
        "partial_recovery_amount": result["partial_recovery_amount"],
    }
