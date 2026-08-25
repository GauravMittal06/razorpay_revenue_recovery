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
    recovery_status: str | None = Query(None),
    outcome: str | None = Query(None),
):
    conn = get_connection()
    try:
        return get_cases(conn, event_type, recovery_status, outcome)
    finally:
        conn.close()


@app.get("/api/cases/{payment_id}")
def case_detail(payment_id: str):
    conn = get_connection()
    try:
        result = get_case_detail(conn, payment_id)
        if result is None:
            raise HTTPException(status_code=404, detail="payment not found")
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
        )
        if result["status"] != "ok":
            raise HTTPException(status_code=400, detail=result.get("error", result["status"]))
        return result
    finally:
        conn.close()


class ReplyBody(BaseModel):
    message: str


@app.post("/api/cases/{payment_id}/reply")
def api_submit_reply(payment_id: str, body: ReplyBody):
    conn = get_connection()
    try:
        result = submit_reply(payment_id, body.message, conn)
        if result.get("status") == "payment_not_found":
            raise HTTPException(status_code=404, detail=result.get("error", "payment not found"))
        return result
    finally:
        conn.close()


@app.post("/api/cases/{payment_id}/simulate-recovery")
def api_simulate_recovery(payment_id: str):
    conn = get_connection()
    try:
        result = simulate_recovery(payment_id, conn)
        if result.get("status") == "payment_not_found":
            raise HTTPException(status_code=404, detail="payment not found")
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