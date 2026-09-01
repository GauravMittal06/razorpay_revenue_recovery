import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
print(f"[server startup] GEMINI_API_KEY loaded: {bool(os.environ.get('GEMINI_API_KEY'))}")

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from backend.db.db import get_connection
from backend.api.queries import get_cases, get_case_detail, get_metrics
from backend.api.actions import trigger_event, submit_reply, simulate_recovery, get_audit_feed

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/cases")
def list_cases(
    event_type: str | None = Query(None),
    status: str | None = Query(None),
    outcome: str | None = Query(None),
):
    conn = get_connection()
    try:
        return get_cases(conn, event_type, status, outcome)
    finally:
        conn.close()


@app.get("/api/cases/{opportunity_id}")
def case_detail(opportunity_id: str):
    conn = get_connection()
    try:
        result = get_case_detail(conn, opportunity_id)
        if result is None:
            raise HTTPException(status_code=404, detail="opportunity not found")
        return result
    finally:
        conn.close()


@app.get("/api/metrics")
def metrics():
    conn = get_connection()
    try:
        return get_metrics(conn)
    finally:
        conn.close()


class TriggerEventBody(BaseModel):
    event_type: str
    amount: int
    root_cause: Optional[str] = None
    customer_id: Optional[str] = None
    days_overdue: Optional[int] = None
    event_id: Optional[str] = None  # upstream idempotency key; omit if none available


@app.post("/api/events/trigger")
def api_trigger_event(body: TriggerEventBody):
    conn = get_connection()
    try:
        result = trigger_event(
            event_type=body.event_type,
            amount=body.amount,
            conn=conn,
            root_cause=body.root_cause,
            customer_id=body.customer_id,
            days_overdue=body.days_overdue,
            event_id=body.event_id,
        )
        # duplicate_event_ignored is a successful idempotent-replay outcome,
        # not an error -- the caller's event was already ingested and no
        # duplicate opportunity was created, exactly as intended.
        if result["status"] not in ("ok", "duplicate_event_ignored"):
            raise HTTPException(status_code=400, detail=result.get("error", result["status"]))
        return result
    finally:
        conn.close()


class ReplyBody(BaseModel):
    message: str


@app.post("/api/cases/{opportunity_id}/reply")
def api_submit_reply(opportunity_id: str, body: ReplyBody):
    conn = get_connection()
    try:
        result = submit_reply(opportunity_id, body.message, conn)
        if result.get("status") == "opportunity_not_found":
            raise HTTPException(status_code=404, detail=result.get("error", "opportunity not found"))
        return result
    finally:
        conn.close()


class SimulateRecoveryBody(BaseModel):
    partial_recovery_amount: Optional[int] = None


@app.post("/api/cases/{opportunity_id}/simulate-recovery")
def api_simulate_recovery(opportunity_id: str, body: SimulateRecoveryBody = SimulateRecoveryBody()):
    conn = get_connection()
    try:
        result = simulate_recovery(opportunity_id, conn, partial_recovery_amount=body.partial_recovery_amount)
        if result.get("status") == "opportunity_not_found":
            raise HTTPException(status_code=404, detail="opportunity not found")
        return result
    finally:
        conn.close()


@app.get("/api/audit-feed")
def api_audit_feed(limit: int = 20):
    conn = get_connection()
    try:
        return get_audit_feed(conn, limit)
    finally:
        conn.close()